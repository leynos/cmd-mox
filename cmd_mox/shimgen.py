"""Utilities for generating command shims."""

from __future__ import annotations

import os
import typing as t
from pathlib import Path

SHIM_PATH = Path(__file__).with_name("shim.py").resolve()


def _check_not_empty(name: str, error_msg: str) -> None:
    """Disallow empty names so we never create ``directory/`` itself."""
    if not name:
        raise ValueError(error_msg)


def _check_not_special_dirs(name: str, error_msg: str) -> None:
    """Reject ``.`` or ``..`` which would resolve to the current or parent directory."""
    if name in {".", ".."}:
        raise ValueError(error_msg)


def _check_no_path_separators(name: str, error_msg: str) -> None:
    """Ensure the name does not include path separators for portability."""
    separators = {"/", "\\", os.sep}
    if os.altsep:
        separators.add(os.altsep)
    if any(sep in name for sep in separators):
        raise ValueError(error_msg)


def _check_no_nul_byte(name: str, error_msg: str) -> None:
    """Reject names containing NUL bytes to avoid filename truncation."""
    if "\x00" in name:
        raise ValueError(error_msg)


def _validate_command_name(name: str) -> None:
    """Validate *name* is a safe command filename."""
    error_msg = f"Invalid command name: {name!r}"

    # Disallow empty names so we never create ``directory/`` itself.
    _check_not_empty(name, error_msg)
    # Reject ``.`` or ``..`` which would resolve to the current or parent directory.
    _check_not_special_dirs(name, error_msg)
    # Ensure the name does not include path separators for portability.
    _check_no_path_separators(name, error_msg)
    # Reject names containing NUL bytes to avoid filename truncation.
    _check_no_nul_byte(name, error_msg)


def create_shim_symlinks(directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
    """Create symlinks for the given commands in *directory*.

    Parameters
    ----------
    directory:
        Directory where symlinks will be created. It must already exist.
    commands:
        Command names (e.g. "git", "curl") for which to create shims.
    """
    if not directory.is_dir():
        msg = f"{directory} is not a directory"
        raise FileNotFoundError(msg)

    if not SHIM_PATH.exists():
        msg = f"Shim template not found: {SHIM_PATH}"
        raise FileNotFoundError(msg)

    if not os.access(SHIM_PATH, os.X_OK):
        try:
            mode = SHIM_PATH.stat().st_mode | 0o111
            SHIM_PATH.chmod(mode)
        except OSError as exc:  # pragma: no cover - OS specific
            msg = f"Cannot make shim executable: {SHIM_PATH}"
            raise PermissionError(msg) from exc
    mapping: dict[str, Path] = {}
    for name in commands:
        _validate_command_name(name)
        link = directory / name
        if os.path.lexists(link):
            if not link.is_symlink():
                msg = f"{link} already exists and is not a symlink"
                raise FileExistsError(msg)
            link.unlink()
        link.symlink_to(SHIM_PATH)
        mapping[name] = link
    return mapping
