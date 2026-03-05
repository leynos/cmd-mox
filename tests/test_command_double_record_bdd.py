"""Behavioural tests for CommandDouble.record() using pytest-bdd."""

from __future__ import annotations

from pathlib import Path

from pytest_bdd import scenario

from tests.steps.command_double_record import *  # noqa: F403

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"


@scenario(
    str(FEATURES_DIR / "command_double_record.feature"),
    "fluent API creates a recording session on a passthrough spy",
)
def test_record_creates_session() -> None:
    """Calling record() on a passthrough spy attaches a started session."""


@scenario(
    str(FEATURES_DIR / "command_double_record.feature"),
    "record without passthrough raises ValueError",
)
def test_record_without_passthrough_raises() -> None:
    """Calling record() without passthrough raises ValueError."""
