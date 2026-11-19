"""Behavioural tests for RecordingSession using pytest-bdd."""

from __future__ import annotations

from pathlib import Path

from pytest_bdd import scenario

from tests.steps.recording_session import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"


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
    "finalizing an unstarted session raises a lifecycle error",
)
def test_finalize_unstarted_session_raises() -> None:
    """An unstarted session refuses to finalize and writes nothing."""


@scenario(
    str(FEATURES_DIR / "recording_session.feature"),
    "the command filter matches command names case-insensitively",
)
def test_command_filter_is_case_insensitive() -> None:
    """Command filtering ignores case and persists only matching commands."""


@scenario(
    str(FEATURES_DIR / "recording_session.feature"),
    "a failed atomic replacement preserves the previous fixture",
)
def test_failed_replacement_preserves_fixture() -> None:
    """A failed save leaves the previous fixture on disk."""


@scenario(
    str(FEATURES_DIR / "recording_session.feature"),
    "loading a fixture with an older schema version migrates it",
)
def test_fixture_migration_from_old_version() -> None:
    """Older schema versions are migrated on load."""
