"""Shared fixture builders for fixture model tests."""

from __future__ import annotations

import typing as typ

from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation
from cmd_mox.record.scrubber import ScrubbingRule


def sample_invocation(*, sequence: int = 0) -> RecordedInvocation:
    """Build a sample ``git status`` recording with the given sequence number."""
    return RecordedInvocation(
        sequence=sequence,
        command="git",
        args=["status", "--short"],
        stdin="",
        env_subset={"GIT_AUTHOR_NAME": "Test User"},
        stdout="M file.py\n",
        stderr="",
        exit_code=0,
        timestamp="2025-01-15T10:30:01Z",
        duration_ms=42,
    )


def sample_metadata() -> FixtureMetadata:
    """Return fixture metadata created with the default factory."""
    return FixtureMetadata.create()


def sample_fixture(
    recordings: list[RecordedInvocation] | None = None,
) -> FixtureFile:
    """Build a fixture with sample metadata and recordings.

    Returns
    -------
    FixtureFile
        A fixture containing the supplied or default recordings.
    """
    return FixtureFile(
        version="1.0.0",
        metadata=sample_metadata(),
        recordings=recordings or [sample_invocation()],
        scrubbing_rules=[],
    )


def fixture_with_rule() -> dict[str, typ.Any]:
    """Return a sample fixture mapping that includes one scrubbing rule."""
    data = sample_fixture().to_dict()
    data["scrubbing_rules"] = [
        ScrubbingRule(pattern="secret", replacement="[redacted]").to_dict()
    ]
    return data
