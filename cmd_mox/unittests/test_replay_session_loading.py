"""ReplaySession loading edge cases.

These pin the behaviour that surrounds the schema itself: path coercion,
retry after a failed load, directory and version errors, and empty fixtures.
They live apart from `test_replay_session.py` so the matching, consumption,
and lifecycle tables there stay focused.
"""

from __future__ import annotations

import json
import typing as typ

import pytest

from cmd_mox.record.replay import ReplaySession
from cmd_mox.unittests._replay_helpers import (
    make_invocation,
    make_recorded_invocation,
    write_fixture_file,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def _raw_fixture_dict() -> dict[str, typ.Any]:
    """Return a minimal but valid fixture mapping for raw-file tests.

    Returns
    -------
    dict[str, typing.Any]
        A fixture mapping that differs from the last one returned.
    """
    return {
        "version": "1.0",
        "metadata": {
            "created_at": "2026-01-15T10:30:00+00:00",
            "cmdmox_version": "0.2.0",
            "platform": "linux",
            "python_version": "3.12.0",
        },
        "recordings": [],
        "scrubbing_rules": [],
    }


class TestReplaySessionLoadGaps:
    """Loading edge cases pinned while testing the replay infrastructure."""

    def test_load_accepts_a_string_path(self, tmp_path: Path) -> None:
        """A ``str`` fixture path is accepted and converted to ``Path``."""
        path = write_fixture_file(tmp_path, [make_recorded_invocation()])

        session = ReplaySession(str(path))
        session.load()

        assert session.fixture_path == path
        assert session.consumed_count == 0

    def test_load_can_be_retried_after_a_failure(self, tmp_path: Path) -> None:
        """A failed load leaves the session unloaded, so a retry succeeds."""
        session = ReplaySession(tmp_path / "late.json")
        with pytest.raises(FileNotFoundError):
            session.load()

        write_fixture_file(tmp_path, [make_recorded_invocation()], filename="late.json")
        session.load()

        assert session.match(make_invocation()) is not None

    def test_load_rejects_a_directory_path(self, tmp_path: Path) -> None:
        """Reading a directory surfaces the underlying ``OSError``."""
        session = ReplaySession(tmp_path)

        with pytest.raises(OSError, match="directory"):
            session.load()

    def test_load_rejects_a_malformed_version(self, tmp_path: Path) -> None:
        """A non-numeric version string is rejected by the version parser."""
        data = _raw_fixture_dict()
        data["version"] = "not-a-version"
        path = tmp_path / "bad_version.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        session = ReplaySession(path)
        with pytest.raises(ValueError, match="Invalid schema version"):
            session.load()

    def test_load_rejects_a_non_string_version(self, tmp_path: Path) -> None:
        """A numeric version field keeps the existing ``expected str`` message."""
        data = _raw_fixture_dict()
        data["version"] = 1
        path = tmp_path / "int_version.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        session = ReplaySession(path)
        with pytest.raises(ValueError, match="expected str"):
            session.load()

    def test_load_accepts_an_empty_recordings_list(self, tmp_path: Path) -> None:
        """An empty fixture loads, never matches, and verifies trivially."""
        path = write_fixture_file(tmp_path, [])
        session = ReplaySession(path)

        session.load()

        assert session.match(make_invocation()) is None
        session.verify_all_consumed()
        assert session.consumed_count == 0
