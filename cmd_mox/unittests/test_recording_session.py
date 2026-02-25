"""Unit tests for RecordingSession lifecycle."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.errors import LifecycleError
from cmd_mox.ipc import Invocation, Response
from cmd_mox.record.fixture import FixtureFile
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
