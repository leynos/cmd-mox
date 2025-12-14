"""Unit tests ensuring the usage guide documents the public API surface."""

from __future__ import annotations

from pathlib import Path

import cmd_mox
from tests.helpers.docs import extract_marked_block

USAGE_GUIDE_PATH = Path(__file__).resolve().parents[1] / "docs" / "usage-guide.md"


def test_usage_guide_api_reference_lists_all_public_symbols() -> None:
    """Every ``cmd_mox.__all__`` export should appear in the usage guide."""
    text = USAGE_GUIDE_PATH.read_text(encoding="utf-8")
    api_reference = extract_marked_block(text, name="api-reference")

    missing = sorted(
        name for name in cmd_mox.__all__ if f"`{name}`" not in api_reference
    )
    assert not missing, f"Missing API reference entries: {', '.join(missing)}"


def test_usage_guide_does_not_reference_removed_platform_helper() -> None:
    """Avoid drift: the docs should not reference deprecated helper names."""
    text = USAGE_GUIDE_PATH.read_text(encoding="utf-8")
    assert "is_supported_platform" not in text
