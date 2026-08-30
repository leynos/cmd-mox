"""Unit tests for placeholder decoding helpers."""

from __future__ import annotations

import hypothesis.strategies as st
import pytest
from hypothesis import given

from tests.helpers import parameters

#: The recognised placeholder tokens, in a stable order for ``sampled_from``.
_TOKENS = sorted(parameters._PLACEHOLDER_TOKENS)

#: Text built by interleaving recognised tokens with arbitrary short runs, so
#: the generated corpus actually contains tokens instead of relying on
#: ``st.text`` stumbling upon one.
_PLACEHOLDER_TEXT = st.lists(
    st.one_of(st.sampled_from(_TOKENS), st.text(max_size=4)),
    max_size=8,
).map("".join)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("hello<space>world", "hello world"),
        ("<SPACE><SPACE>", "  "),
        ("caret<caret>up", "caret^up"),
        ("mix<dq>ed", 'mix"ed'),
        ("<CARET><DQ><SPACE>", '^" '),
        ("<SPACE><space><SPACE>", "   "),
        ("foo<UNKNOWN>bar", "foo<UNKNOWN>bar"),
    ],
    ids=[
        "lowercase-space",
        "repeated-uppercase-space",
        "lowercase-caret",
        "lowercase-double-quote",
        "mixed-case-run",
        "overlapping-mixed-case-spaces",
        "unknown-token-untouched",
    ],
)
def test_decode_placeholders_expands_tokens(raw: str, expected: str) -> None:
    """All supported placeholder tokens should expand to their replacements."""
    assert parameters.decode_placeholders(raw) == expected, "Assertion failed"


def test_decode_placeholders_preserves_empty_string() -> None:
    """Decoding an empty string should return an empty string."""
    assert not parameters.decode_placeholders(""), "Assertion failed"


@given(_PLACEHOLDER_TEXT)
def test_decode_placeholders_leaves_no_recognised_token(raw: str) -> None:
    """No recognised token survives a decode, whatever surrounds it.

    Invariant: for every ``token`` in ``_PLACEHOLDER_TOKENS``,
    ``token not in decode_placeholders(raw)``. This holds because every
    replacement is a single space, caret, or double quote, none of which can
    combine with neighbouring text to spell a fresh ``<...>`` token.
    """
    decoded = parameters.decode_placeholders(raw)
    assert all(token not in decoded for token in _TOKENS), decoded


@given(_PLACEHOLDER_TEXT)
def test_decode_placeholders_is_idempotent(raw: str) -> None:
    """Decoding is a fixpoint after the first application.

    Invariant: ``decode_placeholders(decode_placeholders(raw))`` equals
    ``decode_placeholders(raw)``, which follows from no token surviving the
    first pass.
    """
    once = parameters.decode_placeholders(raw)
    assert parameters.decode_placeholders(once) == once, once
