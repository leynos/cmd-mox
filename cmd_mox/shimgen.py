"""Utilities for generating command shims."""

from __future__ import annotations

import os
import sys
import typing as t
from pathlib import Path

IS_WINDOWS = os.name == "nt"

SHIM_PATH = Path(__file__).with_name("shim.py").resolve()


def _validate_not_empty(name: str, error_msg: str) -> None:
    """Raise ``ValueError`` if *name* is empty."""
    if not name:
        raise ValueError(error_msg)


def _validate_not_dot_directories(name: str, error_msg: str) -> None:
    """Disallow ``.`` and ``..`` which change directory semantics."""
    if name in {".", ".."}:
        raise ValueError(error_msg)


def _validate_no_path_separators(name: str, error_msg: str) -> None:
    """Ensure *name* contains no path separators for portability."""
    separators = {"/", "\\", os.sep}
    if os.altsep:
        separators.add(os.altsep)
    if any(sep in name for sep in separators):
        raise ValueError(error_msg)


def _validate_no_nul_bytes(name: str, error_msg: str) -> None:
    """Reject names containing NUL bytes to avoid truncation."""
    if "\x00" in name:
        raise ValueError(error_msg)


def _validate_command_name(name: str) -> None:
    """Validate *name* is a safe command filename."""
    error_msg = f"Invalid command name: {name!r}"

    validators: list[t.Callable[[str, str], None]] = [
        _validate_not_empty,
        _validate_not_dot_directories,
        _validate_no_path_separators,
        _validate_no_nul_bytes,
    ]
    for validator in validators:
        validator(name, error_msg)


def _format_windows_launcher(python_executable: str, shim_path: Path) -> str:
    """Return the batch script contents to invoke ``shim.py`` on Windows."""
    escaped_python = python_executable.replace('"', '""')
    escaped_shim = os.fspath(shim_path).replace('"', '""')
    return (
        "@echo off\r\n"
        "setlocal ENABLEDELAYEDEXPANSION\r\n"
        f'"{escaped_python}" "{escaped_shim}" %*\r\n'
    )


def _create_windows_shim(directory: Path, name: str) -> Path:
    """Create a ``.cmd`` launcher for *name* that reuses :mod:`cmd_mox.shim`."""
    launcher = directory / f"{name}.cmd"
    if launcher.exists():
        if not launcher.is_file():
            msg = f"{launcher} already exists and is not a file"
            raise FileExistsError(msg)
        launcher.unlink()

    launcher.write_text(
        _format_windows_launcher(sys.executable, SHIM_PATH),
        encoding="utf-8",
        newline="\r\n",
    )
    return launcher


def _create_posix_symlink(directory: Path, name: str) -> Path:
    """Create a POSIX symlink for *name* pointing at :data:`SHIM_PATH`."""
    link = directory / name
    if os.path.lexists(link):
        if not link.is_symlink():
            msg = f"{link} already exists and is not a symlink"
            raise FileExistsError(msg)
        link.unlink()
    link.symlink_to(SHIM_PATH)
    return link


def create_shim_symlinks(directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
    """Create shims for the given commands in *directory*.

    Parameters
    ----------
    directory:
        Directory where shims will be created. It must already exist.
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
        if IS_WINDOWS:
            link = _create_windows_shim(directory, name)
        else:
            link = _create_posix_symlink(directory, name)
        mapping[name] = link
    return mapping
