"""Unit tests for CommandDouble.replay() fluent API."""

from __future__ import annotations

import json
import typing as t

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation
from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation
from cmd_mox.record.replay import ReplaySession

if t.TYPE_CHECKING:
    from pathlib import Path


def _write_fixture(tmp_path: Path, filename: str = "fixture.json") -> Path:
    """Write a minimal valid replay fixture and return its path."""
    fixture = FixtureFile(
        version=FixtureFile.SCHEMA_VERSION,
        metadata=FixtureMetadata.create(),
        recordings=[
            RecordedInvocation(
                sequence=0,
                command="git",
                args=["status"],
                stdin="",
                env_subset={},
                stdout="ok\n",
                stderr="",
                exit_code=0,
                timestamp="2026-01-15T10:30:00+00:00",
                duration_ms=0,
            )
        ],
        scrubbing_rules=[],
    )
    path = tmp_path / filename
    fixture.save(path)
    return path


class TestReplayFluentAPI:
    """Tests for the CommandDouble.replay() fluent method."""

    def test_replay_returns_self(self, tmp_path: Path) -> None:
        """replay() returns the same CommandDouble instance for chaining."""
        mox = CmdMox()
        fixture_path = _write_fixture(tmp_path)

        spy = mox.spy("git")
        result = spy.replay(fixture_path)

        assert result is spy

    @pytest.mark.parametrize(("strict", "expected"), [(True, True), (False, False)])
    def test_replay_configures_matching_mode(
        self, tmp_path: Path, strict: bool, expected: bool
    ) -> None:
        """replay() configures strict or fuzzy matching."""
        mox = CmdMox()
        fixture_path = _write_fixture(tmp_path)

        spy = mox.spy("git").replay(fixture_path, strict=strict)

        session = spy.replay_session
        assert session is not None
        assert session.strict_matching is expected

    def test_replay_accepts_string_path(self, tmp_path: Path) -> None:
        """replay() accepts a string path and converts it to Path."""
        mox = CmdMox()
        fixture_path = _write_fixture(tmp_path)

        spy = mox.spy("git").replay(str(fixture_path))

        session = spy.replay_session
        assert session is not None
        assert session.fixture_path == fixture_path

    def test_replay_loads_session_immediately(self, tmp_path: Path) -> None:
        """replay() loads the fixture eagerly during configuration."""
        mox = CmdMox()
        fixture_path = _write_fixture(tmp_path)

        spy = mox.spy("git").replay(fixture_path)

        session = spy.replay_session
        assert session is not None
        assert session.match(
            Invocation(command="git", args=["status"], stdin="", env={})
        )

    def test_replay_raises_for_missing_fixture(self, tmp_path: Path) -> None:
        """replay() surfaces missing files during setup."""
        mox = CmdMox()

        with pytest.raises(FileNotFoundError):
            mox.spy("git").replay(tmp_path / "missing.json")

    def test_replay_raises_for_invalid_fixture_data(self, tmp_path: Path) -> None:
        """replay() surfaces fixture schema errors during setup."""
        bad_fixture = tmp_path / "fixture.json"
        bad_fixture.write_text(
            json.dumps(
                {
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
            )
        )
        mox = CmdMox()

        with pytest.raises(ValueError, match=r"99\.0"):
            mox.spy("git").replay(bad_fixture)

    def test_replay_rejects_passthrough_combination(self, tmp_path: Path) -> None:
        """replay() rejects passthrough because the behaviours conflict."""
        mox = CmdMox()
        fixture_path = _write_fixture(tmp_path)

        with pytest.raises(ValueError, match=r"replay.*passthrough"):
            mox.spy("git").passthrough().replay(fixture_path)

    def test_replay_raises_when_session_already_attached(self, tmp_path: Path) -> None:
        """replay() rejects attaching a second replay session."""
        mox = CmdMox()
        first = _write_fixture(tmp_path, "first.json")
        second = _write_fixture(tmp_path, "second.json")

        spy = mox.spy("git").replay(first)

        with pytest.raises(RuntimeError, match="already"):
            spy.replay(second)

    @pytest.mark.parametrize("kind", ["stub", "mock"])
    def test_replay_is_only_valid_for_spies(self, kind: str, tmp_path: Path) -> None:
        """replay() is limited to spies until controller integration lands."""
        mox = CmdMox()
        fixture_path = _write_fixture(tmp_path)

        with pytest.raises(ValueError, match=r"replay.*only valid for spies"):
            getattr(mox, kind)("git").replay(fixture_path)


class TestHasReplaySession:
    """Tests for the has_replay_session property."""

    def test_false_by_default(self) -> None:
        """has_replay_session is False when no session is attached."""
        mox = CmdMox()

        assert mox.spy("git").has_replay_session is False

    def test_true_after_replay(self, tmp_path: Path) -> None:
        """has_replay_session is True after replay() is called."""
        mox = CmdMox()
        fixture_path = _write_fixture(tmp_path)

        assert mox.spy("git").replay(fixture_path).has_replay_session is True


class TestReplaySessionProperty:
    """Tests for the replay_session read-only property."""

    def test_none_by_default(self) -> None:
        """replay_session is None when no session is attached."""
        mox = CmdMox()

        assert mox.spy("git").replay_session is None

    def test_returns_session_after_replay(self, tmp_path: Path) -> None:
        """replay_session returns the ReplaySession after replay()."""
        mox = CmdMox()
        fixture_path = _write_fixture(tmp_path)

        session = mox.spy("git").replay(fixture_path).replay_session

        assert session is not None
        assert isinstance(session, ReplaySession)
