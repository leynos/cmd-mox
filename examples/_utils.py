"""Shared helpers for the runnable examples."""

from __future__ import annotations

import shutil


def resolve_command(name: str) -> str:
    """Return an absolute path for *name* when available.

    Returns
    -------
    str
        The resolved executable path, or ``name`` when it is not on ``PATH``.
    """
    return shutil.which(name) or name
