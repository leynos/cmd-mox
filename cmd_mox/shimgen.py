"""Utilities for generating command shims."""

from __future__ import annotations

import typing as t
from pathlib import Path

SHIM_PATH = Path(__file__).with_name("shim.py").resolve()


def create_shim_symlinks(directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
    """Create symlinks for the given commands in *directory*.

    Parameters
    ----------
    directory:
        Directory where symlinks will be created. It must already exist.
    commands:
        Command names (e.g. "git", "curl") for which to create shims.
    """
    if not directory.exists():
        msg = f"Directory {directory} does not exist"
        raise FileNotFoundError(msg)

    SHIM_PATH.chmod(0o755)
    mapping: dict[str, Path] = {}
    for name in commands:
        link = directory / name
        if link.exists():
            link.unlink()
        link.symlink_to(SHIM_PATH)
        mapping[name] = link
    return mapping
