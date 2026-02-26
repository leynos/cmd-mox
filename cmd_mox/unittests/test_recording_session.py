"""Unit tests for RecordingSession lifecycle."""

from __future__ import annotations

import dataclasses as dc
import typing as t

import pytest

from cmd_mox.errors import LifecycleError
from cmd_mox.ipc import Invocation, Response
from cmd_mox.record.fixture import FixtureFile, RecordedInvocation
from cmd_mox.record.session import RecordingSession

if t.TYPE_CHECKING:
    from pathlib import Path


def _make_invocation(
    command: str = "git",
    args: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> Invocation:
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
    return Response(stdout=stdout, stderr=stderr, exit_code=exit_code)


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

    def test_record_before_start_raises(self, tmp_path: Path) -> None:
        """Calling record() before start() raises LifecycleError."""
        session = RecordingSession(tmp_path / "out.json")
        with pytest.raises(LifecycleError):
            session.record(_make_invocation(), _make_response())

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
        assert fixture.version == "1.0"
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


class TestRecordingSessionScrubber:
    """Tests for RecordingSession scrubber integration."""

    def test_scrubber_is_applied_to_recording(self, tmp_path: Path) -> None:
        """A scrubber rewrites the recording before it is stored."""

        class _RedactStdout:
            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
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
            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                return dc.replace(recording, stdout="<redacted>")

        path = tmp_path / "fixture.json"
        session = RecordingSession(path, scrubber=_RedactStdout())
        session.start()
        session.record(_make_invocation(), _make_response(stdout="secret\n"))
        session.finalize()

        loaded = FixtureFile.load(path)
        assert loaded.recordings[0].stdout == "<redacted>"


class TestRecordingSessionDurationValidation:
    """Tests for duration_ms validation in RecordingSession."""

    def test_negative_duration_ms_raises(self, tmp_path: Path) -> None:
        """record() rejects negative duration_ms with ValueError."""
        session = RecordingSession(tmp_path / "out.json")
        session.start()
        with pytest.raises(ValueError, match="non-negative"):
            session.record(_make_invocation(), _make_response(), duration_ms=-1)
