"""pytest-bdd steps that validate user-facing documentation."""

from __future__ import annotations

from pathlib import Path

from pytest_bdd import given, then

import cmd_mox
from tests.helpers.docs import extract_marked_block

DOCS_ROOT = Path(__file__).resolve().parents[2] / "docs"


@given("the CmdMox usage guide is available", target_fixture="usage_guide_text")
def load_usage_guide() -> str:
    """Load the usage guide markdown for assertions."""
    return (DOCS_ROOT / "usage-guide.md").read_text(encoding="utf-8")


@then("the usage guide API reference lists all public cmd_mox symbols")
def assert_usage_guide_api_reference_complete(usage_guide_text: str) -> None:
    """Assert the public API reference mirrors ``cmd_mox.__all__``."""
    api_reference = extract_marked_block(usage_guide_text, name="api-reference")

    missing = sorted(
        name for name in cmd_mox.__all__ if f"`{name}`" not in api_reference
    )
    assert not missing, f"Missing API reference entries: {', '.join(missing)}"
