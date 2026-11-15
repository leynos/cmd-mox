"""Unit tests ensuring the usage guide documents the public API surface."""

from __future__ import annotations

from pathlib import Path

import pytest

import cmd_mox
from tests.helpers.docs import extract_marked_block

USAGE_GUIDE_PATH = Path(__file__).resolve().parents[1] / "docs" / "usage-guide.md"
USERS_GUIDE_PATH = Path(__file__).resolve().parents[1] / "docs" / "users-guide.md"
PLATFORM_DESIGN_LINK = (
    "python-native-command-mocking-design.md"
    "#813-design-decisions-for-the-platform-override"
)


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
    assert "is_supported_platform" not in text, "Assertion failed"


def test_usage_guide_drops_the_windows_shim_override_claim() -> None:
    """The override never affected shim generation or IPC selection."""
    text = USAGE_GUIDE_PATH.read_text(encoding="utf-8")
    assert "Windows-specific shims from a Linux runner" not in text, (
        "the guide must not claim the override selects Windows shims"
    )
    assert PLATFORM_DESIGN_LINK in text, "the guide should link to design 8.13"


def test_usage_guide_marks_the_platform_inputs_deprecated() -> None:
    """Both deprecated inputs are documented as classification-only."""
    text = USAGE_GUIDE_PATH.read_text(encoding="utf-8")
    api_reference = extract_marked_block(text, name="api-reference")
    platform_section = api_reference.split("### Platform helpers", 1)[1]

    normalised = " ".join(platform_section.split())
    expected_fragments = (
        "`PLATFORM_OVERRIDE_ENV`",
        "deprecated name of the platform override env var",
        "never shim generation or IPC transport selection",
    )
    missing = [
        fragment for fragment in expected_fragments if fragment not in normalised
    ]
    assert not missing, f"platform API reference is missing: {missing}"
    assert platform_section.count("`platform` argument is deprecated") == 3, (
        "all three helpers should mark the platform argument deprecated"
    )
    assert PLATFORM_DESIGN_LINK in platform_section, (
        "the API reference should link to design 8.13"
    )


def test_users_guide_documents_platform_support() -> None:
    """The user guide introduces the platform contract and cross-links it."""
    text = USERS_GUIDE_PATH.read_text(encoding="utf-8")
    expected_fragments = (
        "## Platform support",
        "deprecated",
        "(usage-guide.md#platform-helpers)",
        PLATFORM_DESIGN_LINK,
    )
    missing = [fragment for fragment in expected_fragments if fragment not in text]
    assert not missing, f"user guide is missing platform fragments: {missing}"


def test_extract_marked_block_rejects_duplicate_markers() -> None:
    """Guard against ambiguous extraction when multiple blocks exist."""
    text = "<!-- api-reference:start -->one<!-- api-reference:end -->"
    duplicated = f"{text}\n{text}"
    with pytest.raises(ValueError, match="Expected exactly one start marker"):
        extract_marked_block(duplicated, name="api-reference")
