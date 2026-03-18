"""Unit tests for ReplaySession fixture replay."""

from __future__ import annotations

import dataclasses as dc
import json
import typing as t

import pytest

from cmd_mox.errors import LifecycleError, VerificationError
from cmd_mox.ipc import Invocation, Response
from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation
from cmd_mox.record.replay import ReplaySession

if t.TYPE_CHECKING:
    from pathlib import Path


@dc.dataclass
class RecordedInvocationSpec:
    """Optional overrides for building a RecordedInvocation."""

    stdin: str = ""
    env_subset: dict[str, str] = dc.field(default_factory=dict)
    stdout: str = "ok\n"
    stderr: str = ""
    exit_code: int = 0
    sequence: int = 0


def _make_recorded_invocation(
    command: str = "git",
    args: list[str] | None = None,
    spec: RecordedInvocationSpec | None = None,
) -> RecordedInvocation:
    """Build a RecordedInvocation with sensible defaults."""
    s = spec or RecordedInvocationSpec()
    return RecordedInvocation(
        sequence=s.sequence,
        command=command,
        args=["status"] if args is None else args,
        stdin=s.stdin,
        env_subset=s.env_subset,
        stdout=s.stdout,
        stderr=s.stderr,
        exit_code=s.exit_code,
        timestamp="2026-01-15T10:30:00+00:00",
        duration_ms=0,
    )


def _make_fixture_file(
    tmp_path: Path,
    recordings: list[RecordedInvocation] | None = None,
    filename: str = "fixture.json",
) -> Path:
    """Write a fixture file to tmp_path and return the path."""
    recs = [_make_recorded_invocation()] if recordings is None else recordings
    fixture = FixtureFile(
        version=FixtureFile.SCHEMA_VERSION,
        metadata=FixtureMetadata.create(),
        recordings=recs,
        scrubbing_rules=[],
    )
    path = tmp_path / filename
    fixture.save(path)
    return path


def _make_invocation(
    command: str = "git",
    args: list[str] | None = None,
    stdin: str = "",
    env: dict[str, str] | None = None,
) -> Invocation:
    """Build an Invocation with sensible defaults."""
    return Invocation(
        command=command,
        args=["status"] if args is None else args,
        stdin=stdin,
        env=env or {},
    )


def _run_session_match(
    tmp_path: Path,
    recordings: list[RecordedInvocation],
    invocation: Invocation,
    *,
    strict_matching: bool = True,
) -> Response | None:
    """Create a fixture, load a ReplaySession, and return match result."""
    path = _make_fixture_file(tmp_path, recordings)
    session = ReplaySession(path, strict_matching=strict_matching)
    session.load()
    return session.match(invocation)


class TestReplaySessionConstruction:
    """Tests for ReplaySession constructor and properties."""

    def test_fixture_path_property(self, tmp_path: Path) -> None:
        """fixture_path property exposes the configured path."""
        path = tmp_path / "fixture.json"
        session = ReplaySession(path)
        assert session.fixture_path == path

    def test_strict_matching_defaults_to_true(self, tmp_path: Path) -> None:
        """strict_matching defaults to True."""
        session = ReplaySession(tmp_path / "fixture.json")
        assert session.strict_matching is True

    def test_strict_matching_can_be_disabled(self, tmp_path: Path) -> None:
        """strict_matching=False disables strict mode."""
        session = ReplaySession(tmp_path / "f.json", strict_matching=False)
        assert session.strict_matching is False

    def test_allow_unmatched_defaults_to_false(self, tmp_path: Path) -> None:
        """allow_unmatched defaults to False."""
        session = ReplaySession(tmp_path / "fixture.json")
        assert session.allow_unmatched is False

    def test_allow_unmatched_can_be_enabled(self, tmp_path: Path) -> None:
        """allow_unmatched=True enables tolerant verification."""
        session = ReplaySession(tmp_path / "f.json", allow_unmatched=True)
        assert session.allow_unmatched is True

    def test_fixture_not_loaded_initially(self, tmp_path: Path) -> None:
        """_fixture is None before load() is called."""
        session = ReplaySession(tmp_path / "fixture.json")
        assert session._fixture is None


class TestReplaySessionLoad:
    """Tests for ReplaySession.load() fixture loading."""

    def test_load_populates_fixture(self, tmp_path: Path) -> None:
        """After load(), _fixture is a FixtureFile instance."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path)
        session.load()
        assert isinstance(session._fixture, FixtureFile)

    def test_load_rejects_unsupported_version(self, tmp_path: Path) -> None:
        """load() raises ValueError for an unsupported future schema version."""
        data = {
            "version": "99.0",
            "metadata": {
                "created_at": "2026-01-15T10:30:00Z",
                "cmdmox_version": "0.1.0",
                "platform": "linux",
                "python_version": "3.13.0",
            },
            "recordings": [],
            "scrubbing_rules": [],
        }
        path = tmp_path / "future_fixture.json"
        path.write_text(json.dumps(data, indent=2) + "\n")

        session = ReplaySession(path)
        with pytest.raises(ValueError, match=r"99\.0"):
            session.load()

    def test_load_validates_schema(self, tmp_path: Path) -> None:
        """load() handles schema migration for older versions."""
        data = {
            "version": "0.9",
            "metadata": {
                "created_at": "2026-01-15T10:30:00Z",
                "cmdmox_version": "0.1.0",
                "platform": "linux",
                "python_version": "3.13.0",
            },
            "recordings": [
                {
                    "sequence": 0,
                    "command": "echo",
                    "args": ["hello"],
                    "stdin": "",
                    "env_subset": {},
                    "stdout": "hello\n",
                    "stderr": "",
                    "exit_code": 0,
                    "timestamp": "2026-01-15T10:30:01Z",
                    "duration_ms": 5,
                },
            ],
            "scrubbing_rules": [],
        }
        path = tmp_path / "old_fixture.json"
        path.write_text(json.dumps(data, indent=2) + "\n")

        session = ReplaySession(path)
        session.load()
        assert session._fixture is not None
        assert session._fixture.version == "1.0"

    def test_load_file_not_found_raises(self, tmp_path: Path) -> None:
        """load() with a non-existent path raises FileNotFoundError."""
        session = ReplaySession(tmp_path / "missing.json")
        with pytest.raises(FileNotFoundError):
            session.load()

    def test_load_invalid_json_raises(self, tmp_path: Path) -> None:
        """load() with malformed JSON raises an error."""
        path = tmp_path / "bad.json"
        path.write_text("not valid json {{{")
        session = ReplaySession(path)
        with pytest.raises(json.JSONDecodeError):
            session.load()

    def test_load_can_be_called_only_once(self, tmp_path: Path) -> None:
        """Calling load() twice raises LifecycleError."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path)
        session.load()
        with pytest.raises(LifecycleError, match="already loaded"):
            session.load()


class TestReplaySessionStrictMatch:
    """Tests for strict matching mode."""

    def test_match_exact_invocation_returns_response(self, tmp_path: Path) -> None:
        """Exact match returns a Response with correct stdout/stderr/exit_code."""
        rec = _make_recorded_invocation(
            spec=RecordedInvocationSpec(
                stdout="branch: main\n", stderr="warn\n", exit_code=1
            )
        )
        path = _make_fixture_file(tmp_path, [rec])

        session = ReplaySession(path)
        session.load()

        result = session.match(_make_invocation())
        assert result is not None
        assert isinstance(result, Response)
        assert result.stdout == "branch: main\n"
        assert result.stderr == "warn\n"
        assert result.exit_code == 1

    def test_match_wrong_command_returns_none(self, tmp_path: Path) -> None:
        """Different command name returns None."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path)
        session.load()

        result = session.match(_make_invocation(command="curl"))
        assert result is None

    def test_match_wrong_args_returns_none(self, tmp_path: Path) -> None:
        """Same command but different args returns None."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path)
        session.load()

        result = session.match(_make_invocation(args=["pull"]))
        assert result is None

    def test_match_wrong_stdin_returns_none(self, tmp_path: Path) -> None:
        """Matching command and args but different stdin returns None in strict mode."""
        rec = _make_recorded_invocation(
            spec=RecordedInvocationSpec(stdin="expected input")
        )
        path = _make_fixture_file(tmp_path, [rec])

        session = ReplaySession(path)
        session.load()

        result = session.match(_make_invocation(stdin="different input"))
        assert result is None

    def test_match_wrong_env_returns_none(self, tmp_path: Path) -> None:
        """Matching command and args but env_subset mismatch returns None."""
        rec = _make_recorded_invocation(
            spec=RecordedInvocationSpec(env_subset={"GIT_DIR": ".git"})
        )
        path = _make_fixture_file(tmp_path, [rec])

        session = ReplaySession(path)
        session.load()

        # Invocation env has a different value for GIT_DIR
        result = session.match(_make_invocation(env={"GIT_DIR": "/other"}))
        assert result is None

    def test_match_env_subset_semantics(self, tmp_path: Path) -> None:
        """Extra env keys in invocation do not prevent matching."""
        rec = _make_recorded_invocation(
            spec=RecordedInvocationSpec(env_subset={"GIT_DIR": ".git"})
        )
        path = _make_fixture_file(tmp_path, [rec])

        session = ReplaySession(path)
        session.load()

        # Invocation has GIT_DIR plus extra keys -- should still match.
        result = session.match(
            _make_invocation(env={"GIT_DIR": ".git", "EXTRA": "val"})
        )
        assert result is not None


class TestReplaySessionFuzzyMatch:
    """Tests for fuzzy matching mode."""

    @pytest.mark.parametrize(
        ("spec", "invocation_kwargs"),
        [
            (
                RecordedInvocationSpec(stdin="recorded input"),
                {"stdin": "different input"},
            ),
            (
                RecordedInvocationSpec(env_subset={"GIT_DIR": ".git"}),
                {"env": {"TOTALLY": "different"}},
            ),
        ],
        ids=["ignores_stdin", "ignores_env"],
    )
    def test_fuzzy_match_ignores_context(
        self,
        tmp_path: Path,
        spec: RecordedInvocationSpec,
        invocation_kwargs: dict[str, t.Any],
    ) -> None:
        """In fuzzy mode, stdin and env differences do not prevent matching."""
        result = _run_session_match(
            tmp_path,
            [_make_recorded_invocation(spec=spec)],
            _make_invocation(**invocation_kwargs),
            strict_matching=False,
        )
        assert result is not None

    def test_fuzzy_match_still_requires_command(self, tmp_path: Path) -> None:
        """Fuzzy mode still requires command name to match."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path, strict_matching=False)
        session.load()

        result = session.match(_make_invocation(command="curl"))
        assert result is None

    def test_fuzzy_match_still_requires_args(self, tmp_path: Path) -> None:
        """Fuzzy mode still requires args to match."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path, strict_matching=False)
        session.load()

        result = session.match(_make_invocation(args=["pull"]))
        assert result is None


class TestReplaySessionConsumption:
    """Tests for consumed-record tracking."""

    def test_match_marks_recording_as_consumed(self, tmp_path: Path) -> None:
        """After a successful match, the recording index is in _consumed."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path)
        session.load()

        session.match(_make_invocation())
        assert 0 in session._consumed

    def test_consumed_recording_not_matched_again(self, tmp_path: Path) -> None:
        """A consumed recording is skipped on subsequent match() calls."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path)
        session.load()

        first = session.match(_make_invocation())
        second = session.match(_make_invocation())
        assert first is not None
        assert second is None

    def test_multiple_identical_recordings_consumed_sequentially(
        self, tmp_path: Path
    ) -> None:
        """Two identical recordings are consumed one at a time."""
        recs = [
            _make_recorded_invocation(
                spec=RecordedInvocationSpec(sequence=0, stdout="first\n")
            ),
            _make_recorded_invocation(
                spec=RecordedInvocationSpec(sequence=1, stdout="second\n")
            ),
        ]
        path = _make_fixture_file(tmp_path, recs)

        session = ReplaySession(path)
        session.load()

        first = session.match(_make_invocation())
        second = session.match(_make_invocation())
        assert first is not None
        assert first.stdout == "first\n"
        assert second is not None
        assert second.stdout == "second\n"

    def test_match_returns_none_when_all_consumed(self, tmp_path: Path) -> None:
        """When all matching recordings are consumed, match() returns None."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path)
        session.load()

        session.match(_make_invocation())  # consumes the only recording
        result = session.match(_make_invocation())
        assert result is None


class TestReplaySessionVerify:
    """Tests for verify_all_consumed()."""

    def test_verify_all_consumed_passes_when_all_consumed(self, tmp_path: Path) -> None:
        """No error when every recording was consumed."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path)
        session.load()

        session.match(_make_invocation())  # consume the one recording
        session.verify_all_consumed()  # should not raise

    def test_verify_all_consumed_raises_when_unconsumed(self, tmp_path: Path) -> None:
        """Raises VerificationError when not all recordings consumed."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path)
        session.load()

        with pytest.raises(VerificationError, match="unconsumed"):
            session.verify_all_consumed()

    def test_verify_all_consumed_error_message_includes_details(
        self, tmp_path: Path
    ) -> None:
        """The error message includes the command and args of unconsumed recordings."""
        rec = _make_recorded_invocation(command="curl", args=["example.com"])
        path = _make_fixture_file(tmp_path, [rec])

        session = ReplaySession(path)
        session.load()

        with pytest.raises(VerificationError, match=r"curl example\.com"):
            session.verify_all_consumed()

    def test_verify_all_consumed_with_empty_fixture(self, tmp_path: Path) -> None:
        """A fixture with zero recordings passes verification trivially."""
        path = _make_fixture_file(tmp_path, recordings=[])
        session = ReplaySession(path)
        session.load()

        session.verify_all_consumed()  # should not raise


class TestReplaySessionLifecycle:
    """Tests for lifecycle validation."""

    def test_match_before_load_raises(self, tmp_path: Path) -> None:
        """Calling match() before load() raises LifecycleError."""
        session = ReplaySession(tmp_path / "fixture.json")
        with pytest.raises(LifecycleError, match="load"):
            session.match(_make_invocation())

    def test_verify_before_load_raises(self, tmp_path: Path) -> None:
        """Calling verify_all_consumed() before load() raises LifecycleError."""
        session = ReplaySession(tmp_path / "fixture.json")
        with pytest.raises(LifecycleError, match="load"):
            session.verify_all_consumed()


class TestReplaySessionThreadSafety:
    """Tests for ReplaySession thread safety."""

    def test_concurrent_match_produces_unique_consumptions(
        self, tmp_path: Path
    ) -> None:
        """Concurrent match() calls each consume a distinct recording index."""
        import threading

        n_threads = 10
        recs = [
            _make_recorded_invocation(
                spec=RecordedInvocationSpec(sequence=i, stdout=f"out-{i}\n")
            )
            for i in range(n_threads)
        ]
        path = _make_fixture_file(tmp_path, recs)

        session = ReplaySession(path)
        session.load()

        barrier = threading.Barrier(n_threads)
        results = t.cast("list[Response | None]", [None] * n_threads)

        def _match(idx: int) -> None:
            barrier.wait()
            results[idx] = session.match(_make_invocation())

        threads = [threading.Thread(target=_match, args=(i,)) for i in range(n_threads)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # All recordings should be consumed exactly once.
        assert len(session._consumed) == n_threads
        non_none = [r for r in results if r is not None]
        assert len(non_none) == n_threads


class TestReplaySessionAllowUnmatched:
    """Tests for the allow_unmatched flag."""

    def test_allow_unmatched_verify_passes_with_unconsumed(
        self, tmp_path: Path
    ) -> None:
        """When allow_unmatched=True, verify does not raise for unconsumed."""
        path = _make_fixture_file(tmp_path)
        session = ReplaySession(path, allow_unmatched=True)
        session.load()

        # Do not consume any recordings.
        session.verify_all_consumed()  # should not raise

    def test_allow_unmatched_verify_passes_with_partial_consumption(
        self, tmp_path: Path
    ) -> None:
        """When allow_unmatched=True, verify does not raise if some are unconsumed."""
        recs = [
            _make_recorded_invocation(
                spec=RecordedInvocationSpec(sequence=0, stdout="first\n")
            ),
            _make_recorded_invocation(
                spec=RecordedInvocationSpec(sequence=1, stdout="second\n")
            ),
        ]
        path = _make_fixture_file(tmp_path, recs)
        session = ReplaySession(path, allow_unmatched=True)
        session.load()

        # Consume only the first recording, leave the second unconsumed.
        session.match(_make_invocation())
        session.verify_all_consumed()  # should not raise


class TestReplaySessionMatcherDelegation:
    """Tests for ReplaySession delegating to InvocationMatcher."""

    @pytest.mark.parametrize(
        ("strict_matching", "recs", "invocation_kwargs", "expected_stdout"),
        [
            (
                True,
                [
                    _make_recorded_invocation(
                        spec=RecordedInvocationSpec(
                            sequence=0, env_subset={}, stdout="generic\n"
                        )
                    ),
                    _make_recorded_invocation(
                        spec=RecordedInvocationSpec(
                            sequence=1,
                            env_subset={"FOO": "bar"},
                            stdout="specific\n",
                        )
                    ),
                ],
                {"env": {"FOO": "bar", "EXTRA": "val"}},
                "specific\n",
            ),
            (
                False,
                [
                    _make_recorded_invocation(
                        spec=RecordedInvocationSpec(
                            sequence=0, stdin="other", stdout="wrong\n"
                        )
                    ),
                    _make_recorded_invocation(
                        spec=RecordedInvocationSpec(
                            sequence=1, stdin="hello", stdout="right\n"
                        )
                    ),
                ],
                {"stdin": "hello"},
                "right\n",
            ),
        ],
        ids=["strict_env_specificity", "fuzzy_stdin_best_fit"],
    )
    def test_replay_session_best_fit_selection(
        self,
        tmp_path: Path,
        strict_matching: bool,  # noqa: FBT001
        recs: list[RecordedInvocation],
        invocation_kwargs: dict[str, t.Any],
        expected_stdout: str,
    ) -> None:
        """ReplaySession selects the best-fit recording via InvocationMatcher."""
        result = _run_session_match(
            tmp_path,
            recs,
            _make_invocation(**invocation_kwargs),
            strict_matching=strict_matching,
        )
        assert result is not None
        assert result.stdout == expected_stdout
