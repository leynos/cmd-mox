"""Unit tests for placeholder decoding helpers."""

from __future__ import annotations

import pytest

from tests.helpers import parameters


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hello<space>world", "hello world"),
        ("<SPACE><SPACE>", "  "),
        ("caret<caret>up", "caret^up"),
        ("mix<dq>ed", 'mix"ed'),
        ("<CARET><DQ><SPACE>", '^" '),
    ],
)
def test_decode_placeholders_expands_tokens(raw: str, expected: str) -> None:
    """All supported placeholder tokens should expand to their replacements."""
    assert parameters.decode_placeholders(raw) == expected


def test_decode_placeholders_handles_overlapping_tokens() -> None:
    """Overlapping tokens are expanded in sequence without partial loss."""
    raw = "<SPACE><space><SPACE>"
    assert parameters.decode_placeholders(raw) == "   "


def test_decode_placeholders_preserves_empty_string() -> None:
    """Decoding an empty string should return an empty string."""
    assert parameters.decode_placeholders("") == ""
