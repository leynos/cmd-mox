"""Behavioural tests for RecordingSession using pytest-bdd."""

from __future__ import annotations

from pathlib import Path

from pytest_bdd import scenario

from tests.steps.recording_session import *  # noqa: F403

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
