"""Build dotted field paths shared by wire and fixture validation."""

from __future__ import annotations


def join_dotted_path(parent: str, key: str) -> str:
    """Join *parent* and *key* into a dotted field path.

    Parameters
    ----------
    parent : str
        Existing path prefix, or an empty string at the top level.
    key : str
        Field name to append.

    Returns
    -------
    str
        ``parent.key`` when *parent* is non-empty, otherwise *key*.
    """
    return f"{parent}.{key}" if parent else key


__all__ = ["join_dotted_path"]
