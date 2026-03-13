"""Behavioural tests for ReplaySession using pytest-bdd."""

from __future__ import annotations

from pathlib import Path

from pytest_bdd import scenario

from tests.steps.replay_session import *  # noqa: F403

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"


@scenario(
    str(FEATURES_DIR / "replay_session.feature"),
    "replay session loads and matches a recorded invocation",
)
def test_replay_loads_and_matches() -> None:
    """ReplaySession loads a fixture and returns matched response."""


@scenario(
    str(FEATURES_DIR / "replay_session.feature"),
    "replay session tracks consumed recordings",
)
def test_replay_tracks_consumed() -> None:
    """ReplaySession tracks which recordings have been consumed."""


@scenario(
    str(FEATURES_DIR / "replay_session.feature"),
    "replay session returns none for unmatched invocations",
)
def test_replay_rejects_unmatched_strict() -> None:
    """ReplaySession returns None for unmatched invocations."""


@scenario(
    str(FEATURES_DIR / "replay_session.feature"),
    "fuzzy mode ignores stdin and env differences",
)
def test_fuzzy_mode_ignores_stdin_env() -> None:
    """Fuzzy mode matches on command and args only."""


@scenario(
    str(FEATURES_DIR / "replay_session.feature"),
    "verify_all_consumed raises for unconsumed recordings",
)
def test_verify_raises_unconsumed() -> None:
    """verify_all_consumed raises VerificationError when unconsumed."""
