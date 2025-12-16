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

    start_count = text.count(start_marker)
    if start_count != 1:
        msg = (
            f"Expected exactly one start marker ({start_marker}); found {start_count}."
        )
        raise ValueError(msg)

    end_count = text.count(end_marker)
    if end_count != 1:
        msg = f"Expected exactly one end marker ({end_marker}); found {end_count}."
        raise ValueError(msg)

    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if end <= start:
        msg = f"Markers are out of order for {name!r}."
        raise ValueError(msg)

    return text[start + len(start_marker) : end]
