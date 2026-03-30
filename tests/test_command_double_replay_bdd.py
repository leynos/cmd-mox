"""Behavioural tests for CommandDouble.replay() using pytest-bdd."""

from __future__ import annotations

from pathlib import Path

from pytest_bdd import scenario

from tests.steps.command_double_replay import *  # noqa: F403

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"


@scenario(
    str(FEATURES_DIR / "command_double_replay.feature"),
    "fluent API creates a strict replay session on a spy",
)
def test_replay_creates_strict_session() -> None:
    """Calling replay() attaches a loaded strict replay session."""


@scenario(
    str(FEATURES_DIR / "command_double_replay.feature"),
    "replay can use fuzzy matching",
)
def test_replay_can_use_fuzzy_matching() -> None:
    """Calling replay(strict=False) attaches a loaded fuzzy replay session."""


@scenario(
    str(FEATURES_DIR / "command_double_replay.feature"),
    "replay cannot be combined with passthrough",
)
def test_replay_with_passthrough_raises() -> None:
    """Calling replay() after passthrough raises ValueError."""
