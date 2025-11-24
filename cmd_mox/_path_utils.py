"""Shared helpers for normalizing filesystem paths across platforms."""

from __future__ import annotations

import ntpath
import os

IS_WINDOWS = os.name == "nt"


def normalize_path_string(path: str) -> str:
    """Return a normalized string path using platform rules."""
    module = ntpath if IS_WINDOWS else os.path
    normalized = module.normpath(path)
    if IS_WINDOWS:
        normalized = module.normcase(normalized)
    return normalized


def normalize_path(path: os.PathLike[str] | str) -> str:
    """Normalize *path* regardless of whether it is a string or Path."""
    return normalize_path_string(os.fspath(path))
