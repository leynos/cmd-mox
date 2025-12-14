"""Helpers for validating documentation content in tests."""

from __future__ import annotations


def extract_marked_block(text: str, *, name: str) -> str:
    """Return the markdown substring enclosed by ``<!-- name:start -->`` markers.

    Parameters
    ----------
    text:
        Full document content.
    name:
        Marker namespace used in HTML comments.

    Returns
    -------
    str
        The substring between the start and end markers.
    """
    start_marker = f"<!-- {name}:start -->"
    end_marker = f"<!-- {name}:end -->"

    start = text.find(start_marker)
    if start == -1:
        msg = f"Start marker not found: {start_marker}"
        raise ValueError(msg)

    end = text.find(end_marker, start + len(start_marker))
    if end == -1:
        msg = f"End marker not found: {end_marker}"
        raise ValueError(msg)

    return text[start + len(start_marker) : end]
