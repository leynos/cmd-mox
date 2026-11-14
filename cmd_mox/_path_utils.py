"""Shared helpers for normalizing filesystem paths across platforms."""

from __future__ import annotations

import ntpath
import os
import typing as typ

if typ.TYPE_CHECKING:
    import pathlib

IS_WINDOWS = os.name == "nt"


def shim_launcher_path(directory: pathlib.Path, name: str) -> pathlib.Path:
    """Return the path of the shim launcher for *name* inside *directory*.

    Windows shims are ``.cmd`` batch launchers, while POSIX shims are
    suffix-free symlinks. Callers that execute a shim must use this name rule
    rather than assuming the command name is the file name.

    Parameters
    ----------
    directory : pathlib.Path
        Directory holding the generated shims.
    name : str
        Command name represented by the shim.

    Returns
    -------
    pathlib.Path
        Path of the launcher a caller should execute.
    """
    return directory / (f"{name}.cmd" if IS_WINDOWS else name)


def normalize_path_string(path: str) -> str:
    """Return a normalized string path using platform rules.

    Returns
    -------
    str
        The normalized path, using case-folding on Windows.
    """
    module = ntpath if IS_WINDOWS else os.path
    normalized = module.normpath(path)
    if IS_WINDOWS:
        normalized = module.normcase(normalized)
    return normalized


def normalize_path(path: os.PathLike[str] | str) -> str:
    """Normalize *path* regardless of whether it is a string or Path.

    Returns
    -------
    str
        The normalized path string.
    """
    return normalize_path_string(os.fspath(path))
