"""Shared helpers for the runnable examples."""

from __future__ import annotations

import shutil


def resolve_command(name: str) -> str:
    """Return an absolute path for *name* when available."""
    return shutil.which(name) or name
