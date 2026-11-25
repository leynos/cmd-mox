"""Behavioural tests for RecordingSession using pytest-bdd."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_bdd import scenario

from tests.steps.recording_session import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"

_MISSES_OWNER_ONLY_MODE = pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="14.1.2: FixtureFile.save still writes with the process umask",
)
_MISSING_ATOMIC_MODULE = pytest.mark.xfail(
    strict=True,
    raises=ModuleNotFoundError,
    reason="14.1.1: cmd_mox._atomic_write is not implemented yet",
)


@scenario(
    str(FEATURES_DIR / "recording_session.feature"),
    "recording session persists fixture to disk",
)
def test_recording_session_persists_fixture() -> None:
    """Recording session writes a valid fixture file."""


@scenario(
    str(FEATURES_DIR / "recording_session.feature"),
    "environment variables are filtered to safe subset",
)
def test_env_variables_filtered() -> None:
    """Sensitive and system env vars are excluded from recordings."""


@scenario(
    str(FEATURES_DIR / "recording_session.feature"),
    "recording session generates fixture metadata",
)
def test_fixture_metadata_generated() -> None:
    """Fixture metadata captures platform, timestamp, and version."""


@scenario(
    str(FEATURES_DIR / "recording_session.feature"),
    "loading a fixture with an older schema version migrates it",
)
def test_fixture_migration_from_old_version() -> None:
    """Older schema versions are migrated on load."""


@_MISSES_OWNER_ONLY_MODE
@scenario(
    str(FEATURES_DIR / "recording_session.feature"),
    "recorded fixture is readable only by its owner",
)
def test_recorded_fixture_is_owner_only() -> None:
    """A recorded fixture is written atomically and owner-only on POSIX."""


@_MISSING_ATOMIC_MODULE
@scenario(
    str(FEATURES_DIR / "recording_session.feature"),
    "failed fixture save keeps the previous fixture",
)
def test_failed_fixture_save_keeps_previous_fixture() -> None:
    """A failed fixture save leaves the previous fixture intact."""
