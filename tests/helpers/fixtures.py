"""Shared test fixture utilities for creating minimal valid replay fixtures."""

from __future__ import annotations

import typing as t

from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation

if t.TYPE_CHECKING:
    from pathlib import Path


def write_minimal_replay_fixture(
    tmp_path: Path, filename: str = "fixture.json"
) -> Path:
    r"""Write a minimal valid replay fixture and return its path.

    This helper creates a fixture containing a single recorded invocation
    of ``git status`` that returns ``ok\n`` on stdout. It is intended for
    tests that need a valid fixture file but don't care about the specific
    command or response.

    Parameters
    ----------
    tmp_path:
        Temporary directory path (typically from pytest's tmp_path fixture).
    filename:
        Name for the fixture file. Defaults to ``"fixture.json"``.

    Returns
    -------
    Path
        The full path to the created fixture file.
    """
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
