"""Behavioural tests for documentation completeness using pytest-bdd."""

from __future__ import annotations

from pathlib import Path

from pytest_bdd import scenario

from tests.steps import *  # noqa: F403 - re-export pytest-bdd steps

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"


@scenario(
    str(FEATURES_DIR / "documentation.feature"),
    "usage guide lists public API exports",
)
def test_usage_guide_lists_public_api_exports() -> None:
    """The usage guide should list every public export in a single reference."""
    pass
