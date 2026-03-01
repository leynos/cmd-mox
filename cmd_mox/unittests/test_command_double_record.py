"""Unit tests for CommandDouble.record() fluent API."""

from __future__ import annotations

import dataclasses as dc
import typing as t

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.record.session import RecordingSession

if t.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox.record.fixture import RecordedInvocation


class TestRecordFluentAPI:
    """Tests for the CommandDouble.record() fluent method."""

    def test_record_returns_self(self, tmp_path: Path) -> None:
        """record() returns the same CommandDouble instance for chaining."""
        mox = CmdMox()
        spy = mox.spy("git").passthrough()
        result = spy.record(tmp_path / "fixture.json")
        assert result is spy

    def test_record_raises_without_passthrough(self, tmp_path: Path) -> None:
        """record() raises ValueError when passthrough mode is not enabled."""
        mox = CmdMox()
        spy = mox.spy("git")
        with pytest.raises(ValueError, match=r"record.*requires passthrough"):
            spy.record(tmp_path / "fixture.json")

    def test_record_raises_on_stub(self, tmp_path: Path) -> None:
        """record() raises ValueError on a stub double."""
        mox = CmdMox()
        stub = mox.stub("git")
        with pytest.raises(ValueError, match=r"record.*requires passthrough"):
            stub.record(tmp_path / "fixture.json")

    def test_record_raises_on_mock(self, tmp_path: Path) -> None:
        """record() raises ValueError on a mock double."""
        mox = CmdMox()
        mock = mox.mock("git")
        with pytest.raises(ValueError, match=r"record.*requires passthrough"):
            mock.record(tmp_path / "fixture.json")

    def test_record_creates_recording_session(self, tmp_path: Path) -> None:
        """record() creates a RecordingSession on the double."""
        mox = CmdMox()
        spy = mox.spy("git").passthrough().record(tmp_path / "fixture.json")
        assert spy._recording_session is not None
        assert isinstance(spy._recording_session, RecordingSession)

    def test_record_starts_session_immediately(self, tmp_path: Path) -> None:
        """record() calls start() on the session so it is ready to record."""
        mox = CmdMox()
        spy = mox.spy("git").passthrough().record(tmp_path / "fixture.json")
        assert spy._recording_session is not None
        assert spy._recording_session._started_at is not None

    def test_record_forwards_scrubber(self, tmp_path: Path) -> None:
        """record() passes the scrubber parameter to RecordingSession."""

        class _TestScrubber:
            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                return dc.replace(recording, stdout="<scrubbed>")

        scrubber = _TestScrubber()
        mox = CmdMox()
        spy = (
            mox.spy("git")
            .passthrough()
            .record(
                tmp_path / "fixture.json",
                scrubber=scrubber,
            )
        )
        assert spy._recording_session is not None
        assert spy._recording_session._scrubber is scrubber

    def test_record_forwards_env_allowlist(self, tmp_path: Path) -> None:
        """record() passes the env_allowlist parameter to RecordingSession."""
        mox = CmdMox()
        spy = (
            mox.spy("git")
            .passthrough()
            .record(
                tmp_path / "fixture.json",
                env_allowlist=["GIT_AUTHOR_NAME", "GIT_DIR"],
            )
        )
        assert spy._recording_session is not None
        assert spy._recording_session._env_allowlist == [
            "GIT_AUTHOR_NAME",
            "GIT_DIR",
        ]

    def test_record_accepts_string_path(self, tmp_path: Path) -> None:
        """record() accepts a string path and converts it to Path."""
        mox = CmdMox()
        path_str = str(tmp_path / "fixture.json")
        spy = mox.spy("git").passthrough().record(path_str)
        assert spy._recording_session is not None
        assert spy._recording_session.fixture_path == tmp_path / "fixture.json"


class TestHasRecordingSession:
    """Tests for the has_recording_session property."""

    def test_false_by_default(self) -> None:
        """has_recording_session is False when no session is attached."""
        mox = CmdMox()
        spy = mox.spy("git")
        assert spy.has_recording_session is False

    def test_true_after_record(self, tmp_path: Path) -> None:
        """has_recording_session is True after record() is called."""
        mox = CmdMox()
        spy = mox.spy("git").passthrough().record(tmp_path / "fixture.json")
        assert spy.has_recording_session is True
