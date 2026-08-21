"""Shared helpers for the runnable examples."""

from __future__ import annotations

import shutil


def resolve_command(name: str) -> str:
    """Return a resolved path for *name*, falling back to *name* when unavailable.

    Returns
    -------
    str
        The resolved executable path, or ``name`` when it is not on ``PATH``.
    """
    return shutil.which(name) or name
