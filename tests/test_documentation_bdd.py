"""Behavioural tests for documentation completeness using pytest-bdd."""

from __future__ import annotations

from pathlib import Path

from pytest_bdd import scenario

from tests.steps import *  # ruff: ignore[undefined-local-with-import-star] - pytest-bdd registers step definitions through star imports

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"


@scenario(
    str(FEATURES_DIR / "documentation.feature"),
    "user's guide lists public API exports",
)
def test_users_guide_lists_public_api_exports() -> None:
    """The user's guide should list every public export in a single reference."""
    pass


@scenario(
    str(FEATURES_DIR / "documentation.feature"),
    "published examples are declared and runnable",
)
def test_published_examples_are_declared_and_runnable() -> None:
    """Published examples are all marked, and the README quick start runs."""
    pass
