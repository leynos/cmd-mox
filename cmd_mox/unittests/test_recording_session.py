"""Unit tests for RecordingSession lifecycle."""

from __future__ import annotations

import dataclasses as dc
import threading
import typing as typ

import pytest

from cmd_mox.atomic_write import AtomicWriteError
from cmd_mox.errors import LifecycleError
from cmd_mox.ipc import Invocation, Response
from cmd_mox.record.fixture import FixtureFile, RecordedInvocation
from cmd_mox.record.session import RecordingSession

if typ.TYPE_CHECKING:
    from pathlib import Path


def _make_invocation(
    command: str = "git",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> Invocation:
    """Build an ``Invocation`` defaulting to ``git status`` with a sample env."""
    return Invocation(
        command=command,
        args=args or ["status"],
        stdin="",
        env=env or {"GIT_AUTHOR_NAME": "Tester", "PATH": "/usr/bin"},
    )


def _make_response(
    stdout: str = "ok\n",
    stderr: str = "",
    exit_code: int = 0,
) -> Response:
    """Build a ``Response`` with the given output streams and exit code."""
    return Response(stdout=stdout, stderr=stderr, exit_code=exit_code)


def _join_all(threads: list[threading.Thread], *, timeout: float = 10.0) -> None:
    """Join every thread, failing the test if any is still running."""
    for thread in threads:
        thread.join(timeout)
    assert not any(thread.is_alive() for thread in threads), (
        "worker thread did not finish; the operation may be deadlocked"
    )


class TestRecordingSessionLifecycle:
    """Tests for RecordingSession start/record/finalize lifecycle."""

    def test_fixture_path_property(self, tmp_path: Path) -> None:
        """fixture_path property exposes the configured fixture path."""
        path = tmp_path / "out.json"
        session = RecordingSession(path)
        assert session.fixture_path == path

    def test_start_sets_started_at(self, tmp_path: Path) -> None:
        """After start(), the session has a non-None _started_at."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        assert session._started_at is not None

    def test_is_started_false_before_start(self, tmp_path: Path) -> None:
        """is_started is False before start() is called."""
        session = RecordingSession(tmp_path / "out.json")
        assert session.is_started is False

    def test_is_started_true_after_start(self, tmp_path: Path) -> None:
        """is_started is True after start() is called."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        assert session.is_started is True

    def test_record_before_start_raises(self, tmp_path: Path) -> None:
        """Calling record() before start() raises LifecycleError."""
        session = RecordingSession(tmp_path / "out.json")
        with pytest.raises(LifecycleError):
            session.record(_make_invocation(), _make_response())

    def test_finalize_before_start_raises_without_writing(self, tmp_path: Path) -> None:
        """finalize() rejects an unstarted session and writes no file."""
        path = tmp_path / "out.json"
        session = RecordingSession(path)

        with pytest.raises(LifecycleError):
            session.finalize()

        assert path.exists() is False

    def test_record_appends_invocation(self, tmp_path: Path) -> None:
        """After record(), the session contains one recorded invocation."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.record(_make_invocation(), _make_response())
        assert len(session._recordings) == 1

    def test_record_assigns_sequence_numbers(self, tmp_path: Path) -> None:
        """Multiple record() calls produce sequential 0-based sequence numbers."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        for _ in range(3):
            session.record(_make_invocation(), _make_response())

        sequences = [r.sequence for r in session._recordings]
        assert sequences == [0, 1, 2]

    def test_record_captures_timestamp(self, tmp_path: Path) -> None:
        """Each recorded invocation has a non-empty ISO8601 timestamp."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.record(_make_invocation(), _make_response())

        ts = session._recordings[0].timestamp
        assert ts  # non-empty
        # Basic ISO8601 format check: contains a 'T' separator
        assert "T" in ts

    def test_record_accepts_duration_ms(self, tmp_path: Path) -> None:
        """record(duration_ms=42) stores the value on the recording."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.record(_make_invocation(), _make_response(), duration_ms=42)
        assert session._recordings[0].duration_ms == 42

    def test_record_defaults_duration_ms_to_zero(self, tmp_path: Path) -> None:
        """record() without duration_ms defaults to 0."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.record(_make_invocation(), _make_response())
        assert session._recordings[0].duration_ms == 0


class TestRecordingSessionFinalize:
    """Tests for RecordingSession.finalize() persistence."""

    def test_finalize_returns_fixture_file(self, tmp_path: Path) -> None:
        """finalize() returns a FixtureFile with correct metadata and recordings."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.record(_make_invocation(), _make_response())

        fixture = session.finalize()

        assert isinstance(fixture, FixtureFile)
        assert fixture.version == "1.0.0"
        assert len(fixture.recordings) == 1
        assert fixture.recordings[0].command == "git"

    def test_finalize_saves_to_disk(self, tmp_path: Path) -> None:
        """After finalize(), the fixture file exists at the configured path."""
        path = tmp_path / "persisted.json"
        session = RecordingSession(path)
        session.start()
        session.record(_make_invocation(), _make_response())
        session.finalize()

        assert path.exists()
        loaded = FixtureFile.load(path)
        assert len(loaded.recordings) == 1

    def test_finalize_is_idempotent(self, tmp_path: Path) -> None:
        """Calling finalize() twice returns the same fixture without error."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.record(_make_invocation(), _make_response())

        first = session.finalize()
        second = session.finalize()
        assert first is second

    def test_record_after_finalize_raises(self, tmp_path: Path) -> None:
        """Calling record() after finalize() raises LifecycleError."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.finalize()

        with pytest.raises(LifecycleError):
            session.record(_make_invocation(), _make_response())

    def test_start_after_finalize_raises(self, tmp_path: Path) -> None:
        """A finalized session cannot be restarted."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.finalize()

        with pytest.raises(LifecycleError):
            session.start()


class TestRecordingSessionEnvFiltering:
    """Tests for environment variable filtering in RecordingSession."""

    def test_env_subset_excludes_path(self, tmp_path: Path) -> None:
        """PATH is excluded from the recorded env_subset."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.record(
            _make_invocation(env={"PATH": "/usr/bin", "GIT_DIR": ".git"}),
            _make_response(),
        )
        env = session._recordings[0].env_subset
        assert "PATH" not in env
        assert "GIT_DIR" in env

    def test_env_subset_includes_allowlisted_keys(self, tmp_path: Path) -> None:
        """Keys on the allowlist are included even if otherwise excluded."""
        session = RecordingSession(
            tmp_path / "out.json",
            env_allowlist=["MY_CUSTOM_KEY"],
        )
        session.start()
        session.record(
            _make_invocation(env={"MY_CUSTOM_KEY": "val", "PATH": "/bin"}),
            _make_response(),
        )
        env = session._recordings[0].env_subset
        assert "MY_CUSTOM_KEY" in env
        assert "PATH" not in env

    def test_unknown_command_records_no_unrequested_keys(self, tmp_path: Path) -> None:
        """A command without a known prefix records no unrequested keys."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.record(
            _make_invocation(
                command="greet",
                env={"MY_SETTING": "enabled", "HOSTISH_VALUE": "host"},
            ),
            _make_response(),
        )

        assert session._recordings[0].env_subset == {}

    def test_explicit_keys_override_system_and_sensitive_exclusions(
        self, tmp_path: Path
    ) -> None:
        """Explicit keys are recorded despite system or sensitive names."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.record(
            _make_invocation(
                command="greet",
                env={"SECRET_TOKEN": "tok", "PATH": "/usr/bin"},
            ),
            _make_response(),
            explicit_keys=["SECRET_TOKEN", "PATH"],
        )

        assert session._recordings[0].env_subset == {
            "SECRET_TOKEN": "tok",
            "PATH": "/usr/bin",
        }

    def test_explicit_keys_do_not_override_internal_exclusion(
        self, tmp_path: Path
    ) -> None:
        """Explicit keys never let a CmdMox-internal key into a recording."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        session.record(
            _make_invocation(
                command="greet",
                env={"CMOX_IPC_SOCKET": "socket"},
            ),
            _make_response(),
            explicit_keys=["CMOX_IPC_SOCKET"],
        )

        assert session._recordings[0].env_subset == {}


class TestRecordingSessionCommandFilter:
    """Tests for RecordingSession command_filter behavior."""

    def test_command_filter_includes_matching_command(self, tmp_path: Path) -> None:
        """Invocations matching the filter are recorded."""
        session = RecordingSession(tmp_path / "out.json", command_filter="git")
        session.start()
        session.record(_make_invocation(command="git"), _make_response())
        assert len(session._recordings) == 1
        assert session._recordings[0].command == "git"

    def test_command_filter_excludes_non_matching_command(self, tmp_path: Path) -> None:
        """Invocations not matching the filter are silently skipped."""
        session = RecordingSession(tmp_path / "out.json", command_filter="git")
        session.start()
        session.record(_make_invocation(command="echo"), _make_response())
        assert len(session._recordings) == 0

    def test_command_filter_mixed(self, tmp_path: Path) -> None:
        """Only matching commands survive filtering through to finalize."""
        session = RecordingSession(tmp_path / "out.json", command_filter="git")
        session.start()
        session.record(
            _make_invocation(command="git"), _make_response(stdout="git-ok\n")
        )
        session.record(
            _make_invocation(command="echo"),
            _make_response(stdout="echo-ok\n"),
        )
        fixture = session.finalize()
        assert len(fixture.recordings) == 1
        assert fixture.recordings[0].command == "git"

    def test_command_filter_list_input_is_copied(self, tmp_path: Path) -> None:
        """Mutating the original list after construction has no effect."""
        cmds = ["git"]
        session = RecordingSession(tmp_path / "out.json", command_filter=cmds)
        cmds.append("echo")
        session.start()
        session.record(_make_invocation(command="echo"), _make_response())
        assert len(session._recordings) == 0

    def test_command_filter_matches_case_insensitively(self, tmp_path: Path) -> None:
        """Filter entries and command names are compared without case."""
        session = RecordingSession(tmp_path / "out.json", command_filter="GIT")
        session.start()
        session.record(_make_invocation(command="git"), _make_response())

        assert len(session._recordings) == 1

    def test_command_filter_normalizes_every_list_entry(self, tmp_path: Path) -> None:
        """Every entry in a filter list is normalized once at construction."""
        session = RecordingSession(
            tmp_path / "out.json", command_filter=["Git", "ECHO"]
        )
        session.start()
        session.record(_make_invocation(command="echo"), _make_response())
        session.record(_make_invocation(command="git"), _make_response())

        assert [recording.command for recording in session._recordings] == [
            "echo",
            "git",
        ]

    def test_empty_command_filter_disables_filtering(self, tmp_path: Path) -> None:
        """An empty list means no filter, not a filter that matches nothing."""
        session = RecordingSession(tmp_path / "out.json", command_filter=[])
        session.start()
        session.record(_make_invocation(command="git"), _make_response())
        session.record(_make_invocation(command=""), _make_response())

        assert len(session._recordings) == 2

    def test_none_command_filter_disables_filtering(self, tmp_path: Path) -> None:
        """None means no filter, so every command is recorded."""
        session = RecordingSession(tmp_path / "out.json", command_filter=None)
        session.start()
        session.record(_make_invocation(command="git"), _make_response())

        assert len(session._recordings) == 1

    def test_empty_string_filter_matches_only_an_empty_command(
        self, tmp_path: Path
    ) -> None:
        """An empty filter entry is a real filter for the empty command name."""
        session = RecordingSession(tmp_path / "out.json", command_filter="")
        session.start()
        session.record(_make_invocation(command="git"), _make_response())
        session.record(_make_invocation(command=""), _make_response())

        assert [recording.command for recording in session._recordings] == [""]


class TestRecordingSessionScrubber:
    """Tests for RecordingSession scrubber integration."""

    def test_scrubber_is_applied_to_recording(self, tmp_path: Path) -> None:
        """A scrubber rewrites the recording before it is stored."""

        class _RedactStdout:
            """Scrubber double that replaces stdout with a redaction marker."""

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Return a copy of the recording with stdout redacted."""
                return dc.replace(recording, stdout="<redacted>")

        session = RecordingSession(tmp_path / "out.json", scrubber=_RedactStdout())
        session.start()
        original_response = _make_response(stdout="secret output\n")
        session.record(_make_invocation(), original_response)

        # Original response object is untouched.
        assert original_response.stdout == "secret output\n"
        # Stored recording reflects the scrubbed value.
        assert session._recordings[0].stdout == "<redacted>"

    def test_scrubber_result_is_persisted(self, tmp_path: Path) -> None:
        """Scrubbed data is what ends up in the persisted fixture file."""

        class _RedactStdout:
            """Scrubber double that replaces stdout with a redaction marker."""

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Return a copy of the recording with stdout redacted."""
                return dc.replace(recording, stdout="<redacted>")

        path = tmp_path / "fixture.json"
        session = RecordingSession(path, scrubber=_RedactStdout())
        session.start()
        session.record(_make_invocation(), _make_response(stdout="secret\n"))
        session.finalize()

        loaded = FixtureFile.load(path)
        assert loaded.recordings[0].stdout == "<redacted>"

    def test_explicit_sensitive_key_is_scrubbed_before_persistence(
        self, tmp_path: Path
    ) -> None:
        """A sensitive explicit key is scrubbed, not persisted in clear text."""

        class _RedactToken:
            """Scrubber double that observes recordings and redacts API_TOKEN."""

            def __init__(self) -> None:
                """Start with no observed recordings."""
                self.seen: list[RecordedInvocation] = []

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Retain the incoming recording and redact the token value."""
                self.seen.append(recording)
                redacted = {
                    key: "<redacted>" if key == "API_TOKEN" else value
                    for key, value in recording.env_subset.items()
                }
                return dc.replace(recording, env_subset=redacted)

        scrubber = _RedactToken()
        path = tmp_path / "fixture.json"
        session = RecordingSession(path, scrubber=scrubber)
        session.start()
        session.record(
            _make_invocation(env={"API_TOKEN": "tok123"}),
            _make_response(),
            explicit_keys=["API_TOKEN"],
        )
        session.finalize()

        # The sensitive value reaches the scrubber, which is the only place a
        # caller-supplied redaction rule can act on it.
        assert scrubber.seen[0].env_subset == {"API_TOKEN": "tok123"}
        loaded = FixtureFile.load(path)
        assert loaded.recordings[0].env_subset == {"API_TOKEN": "<redacted>"}


class TestRecordingSessionDurationValidation:
    """Tests for duration_ms validation in RecordingSession."""

    def test_negative_duration_ms_raises(self, tmp_path: Path) -> None:
        """record() rejects negative duration_ms with ValueError."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        with pytest.raises(ValueError, match="non-negative"):
            session.record(_make_invocation(), _make_response(), duration_ms=-1)


class TestRecordingSessionThreadSafety:
    """Tests for RecordingSession thread safety."""

    def test_concurrent_record_produces_unique_sequences(self, tmp_path: Path) -> None:
        """Concurrent record() calls must assign unique sequence numbers."""
        import threading

        session = RecordingSession(tmp_path / "out.json")
        session.start()

        barrier = threading.Barrier(10)

        def _record() -> None:
            """Record one invocation once every thread has reached the barrier."""
            barrier.wait()
            session.record(_make_invocation(), _make_response())

        threads = [threading.Thread(target=_record) for _ in range(10)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert len(session._recordings) == 10
        sequences = sorted(r.sequence for r in session._recordings)
        assert sequences == list(range(10))


class TestRecordingSessionConcurrency:
    """Tests for finalize and scrub concurrency guarantees."""

    def test_concurrent_finalize_returns_one_object_and_saves_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Concurrent finalize calls share one fixture and one write."""
        path = tmp_path / "out.json"
        session = RecordingSession(path)
        session.start()
        session.record(_make_invocation(), _make_response())

        save_calls: list[Path] = []
        original_save = FixtureFile.save

        def _counting_save(fixture: FixtureFile, destination: Path) -> None:
            """Record the destination, then delegate to the real save."""
            save_calls.append(destination)
            original_save(fixture, destination)

        monkeypatch.setattr(FixtureFile, "save", _counting_save)

        worker_count = 8
        barrier = threading.Barrier(worker_count)
        results: list[FixtureFile] = []
        errors: list[Exception] = []

        def _finalize() -> None:
            """Wait for every worker, then finalize the shared session."""
            try:
                barrier.wait(timeout=5)
                results.append(session.finalize())
            except Exception as exc:  # ruff: ignore[blind-except] - surfaced by the assertions
                errors.append(exc)

        threads = [
            threading.Thread(target=_finalize, daemon=True) for _ in range(worker_count)
        ]
        for thread in threads:
            thread.start()
        _join_all(threads)

        assert errors == []
        assert len(save_calls) == 1
        assert len(results) == worker_count
        first = results[0]
        assert all(result is first for result in results)
        assert len(FixtureFile.load(path).recordings) == 1

    def test_finalize_retries_after_a_save_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed save leaves the session finalized but retryable."""
        path = tmp_path / "out.json"
        session = RecordingSession(path)
        session.start()
        session.record(_make_invocation(), _make_response())

        attempts: list[Path] = []
        original_save = FixtureFile.save

        def _flaky_save(fixture: FixtureFile, destination: Path) -> None:
            """Fail the first write and delegate on later attempts."""
            attempts.append(destination)
            if len(attempts) == 1:
                message = "disk full"
                raise AtomicWriteError(message, path=destination)
            original_save(fixture, destination)

        monkeypatch.setattr(FixtureFile, "save", _flaky_save)

        with pytest.raises(AtomicWriteError):
            session.finalize()

        assert path.exists() is False
        with pytest.raises(LifecycleError):
            session.record(_make_invocation(), _make_response())

        fixture = session.finalize()

        assert len(attempts) == 2
        assert len(fixture.recordings) == 1
        assert len(FixtureFile.load(path).recordings) == 1
        assert session.finalize() is fixture

    def test_scrubber_may_call_back_into_the_session(self, tmp_path: Path) -> None:
        """A scrubber that calls a locked session method does not deadlock."""
        session = RecordingSession(tmp_path / "out.json")

        class _ReentrantScrubber:
            """Scrubber double that restarts the session during scrubbing."""

            def __init__(self) -> None:
                """Leave the session reference unset until construction ends."""
                self.session: RecordingSession | None = None

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Call a lock-taking session method, then return the recording."""
                assert self.session is not None
                self.session.start()
                return recording

        scrubber = _ReentrantScrubber()
        scrubber.session = session
        session._scrubber = scrubber
        session.start()

        errors: list[Exception] = []

        def _record() -> None:
            """Record one invocation, capturing any failure for the assertion."""
            try:
                session.record(_make_invocation(), _make_response())
            except Exception as exc:  # ruff: ignore[blind-except] - surfaced by the assertions
                errors.append(exc)

        thread = threading.Thread(target=_record, daemon=True)
        thread.start()
        _join_all([thread])

        assert errors == []
        assert len(session._recordings) == 1

    def test_finalize_during_a_scrub_rejects_the_recording(
        self, tmp_path: Path
    ) -> None:
        """Finalizing inside scrub() rejects the in-flight recording."""
        path = tmp_path / "out.json"
        session_holder: list[RecordingSession] = []

        class _FinalizingScrubber:
            """Scrubber double that finalizes the session from inside scrub()."""

            fixture: FixtureFile | None = None

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Finalize the session, then return the recording."""
                self.fixture = session_holder[0].finalize()
                return recording

        scrubber = _FinalizingScrubber()
        session = RecordingSession(path, scrubber=scrubber)
        session_holder.append(session)
        session.start()

        errors: list[Exception] = []

        def _record() -> None:
            """Record one invocation, capturing the expected lifecycle error."""
            try:
                session.record(_make_invocation(), _make_response())
            except Exception as exc:  # ruff: ignore[blind-except] - surfaced by the assertions
                errors.append(exc)

        thread = threading.Thread(target=_record, daemon=True)
        thread.start()
        _join_all([thread])

        assert len(errors) == 1
        assert isinstance(errors[0], LifecycleError)
        assert session._recordings == []
        assert scrubber.fixture is not None
        assert scrubber.fixture.recordings == []

    def test_scrubber_exception_propagates_without_consuming_a_sequence(
        self, tmp_path: Path
    ) -> None:
        """A failing scrub leaves no gap in the append-order sequence."""

        class _FlakyScrubber:
            """Scrubber double that fails while its flag is set."""

            should_fail = True

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Raise on demand; otherwise return the recording unchanged."""
                if self.should_fail:
                    message = "scrub failed"
                    raise RuntimeError(message)
                return recording

        scrubber = _FlakyScrubber()
        session = RecordingSession(tmp_path / "out.json", scrubber=scrubber)
        session.start()

        with pytest.raises(RuntimeError, match="scrub failed"):
            session.record(_make_invocation(), _make_response())

        assert session._recordings == []

        scrubber.should_fail = False
        session.record(_make_invocation(), _make_response())

        assert [recording.sequence for recording in session._recordings] == [0]

    def test_scrubber_cannot_override_the_assigned_sequence(
        self, tmp_path: Path
    ) -> None:
        """The session assigns the append-order sequence after scrubbing."""

        class _SequenceScrubber:
            """Scrubber double that tries to pin its own sequence number."""

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Return a copy with a deliberately wrong sequence."""
                return dc.replace(recording, sequence=99)

        session = RecordingSession(tmp_path / "out.json", scrubber=_SequenceScrubber())
        session.start()
        session.record(_make_invocation(), _make_response())
        session.record(_make_invocation(), _make_response())

        assert [recording.sequence for recording in session._recordings] == [0, 1]

    def test_scrubbers_run_outside_the_session_lock(self, tmp_path: Path) -> None:
        """Two scrubbers can run concurrently without holding the state lock."""
        barrier = threading.Barrier(2)

        class _BarrierScrubber:
            """Scrubber double that waits for a second concurrent scrub."""

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Wait for the peer scrub, then return the recording."""
                barrier.wait(timeout=5)
                return recording

        session = RecordingSession(tmp_path / "out.json", scrubber=_BarrierScrubber())
        session.start()
        errors: list[Exception] = []

        def _record() -> None:
            """Record one invocation, capturing any failure for the assertion."""
            try:
                session.record(_make_invocation(), _make_response())
            except Exception as exc:  # ruff: ignore[blind-except] - surfaced by the assertions
                errors.append(exc)

        threads = [threading.Thread(target=_record, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        _join_all(threads)

        assert errors == []
        assert len(session._recordings) == 2
        assert sorted(recording.sequence for recording in session._recordings) == [
            0,
            1,
        ]


class TestRecordingSessionEnvironmentFiltering:
    """End-to-end tests for the environment subset persisted by a session."""

    @staticmethod
    def _record_go_session(
        path: Path,
        *,
        env_allowlist: list[str] | None = None,
    ) -> FixtureFile:
        """Record one Go invocation, finalize, and reload the saved fixture."""
        session = RecordingSession(path, env_allowlist=env_allowlist)
        session.start()
        session.record(
            _make_invocation(
                command="go",
                env={
                    "GOPATH": "/work",
                    "GORACE": "halt_on_error=1",
                    "GOOGLE_CLOUD_PROJECT": "proj",
                    "PATH": "/usr/bin",
                },
            ),
            _make_response(),
        )
        session.finalize()
        return FixtureFile.load(path)

    def test_go_recording_keeps_documented_names_and_drops_lookalikes(
        self,
        tmp_path: Path,
    ) -> None:
        """A saved Go recording keeps documented names, not GOOGLE_* names."""
        fixture = self._record_go_session(tmp_path / "go.json")

        assert fixture.recordings[0].env_subset == {
            "GOPATH": "/work",
            "GORACE": "halt_on_error=1",
        }, "a Go recording should keep GOPATH and GORACE, not GOOGLE_CLOUD_PROJECT"

    def test_go_recording_keeps_allowlisted_lookalike(
        self,
        tmp_path: Path,
    ) -> None:
        """An allowlisted lookalike is persisted in the saved fixture."""
        fixture = self._record_go_session(
            tmp_path / "go-allow.json",
            env_allowlist=["GOOGLE_CLOUD_PROJECT"],
        )

        assert fixture.recordings[0].env_subset == {
            "GOPATH": "/work",
            "GORACE": "halt_on_error=1",
            "GOOGLE_CLOUD_PROJECT": "proj",
        }, "an allowlisted lookalike should be recorded alongside the Go names"
