"""Utilities for generating command shims."""

from __future__ import annotations

import logging
import os
import sys
import typing as t
from pathlib import Path

from cmd_mox import _path_utils as path_utils
from cmd_mox.fs_retry import DEFAULT_UNLINK_RETRY, retry_unlink

SHIM_PATH = Path(__file__).with_name("shim.py").resolve()
LAUNCHER_RETRY = DEFAULT_UNLINK_RETRY
logger = logging.getLogger(__name__)


def _escape_batch_literal(value: str) -> str:
    """Return *value* escaped for safe inclusion inside batch quotes."""
    escaped = value.replace("^", "^^").replace("%", "%%")
    return escaped.replace('"', '""')


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


def _normalize_command_name(name: str) -> str:
    """Return a filesystem-safe comparison key for *name*."""
    return name.casefold() if path_utils.IS_WINDOWS else name


def _validate_command_uniqueness(commands: list[str]) -> None:
    """Ensure *commands* do not collide when case-insensitive filesystems apply."""
    seen: set[str] = set()
    for name in commands:
        key = _normalize_command_name(name)
        if key in seen:
            msg = (
                "Duplicate command names detected on a case-insensitive filesystem: "
                f"{name!r}"
            )
            raise ValueError(msg)
        seen.add(key)


def _format_windows_launcher(python_executable: str, shim_path: Path) -> str:
    """Return the batch script contents to invoke ``shim.py`` on Windows."""
    escaped_python = _escape_batch_literal(python_executable)
    escaped_shim = _escape_batch_literal(os.fspath(shim_path))
    return (
        "@echo off\n"
        ":: Delayed expansion is disabled to preserve literal exclamation marks in\n"
        ":: user arguments. Enabling it would consume carets during %* expansion,\n"
        ":: changing the argv seen by Python when shims pass arguments through.\n"
        "setlocal ENABLEEXTENSIONS DISABLEDELAYEDEXPANSION\n"
        'set "CMOX_SHIM_COMMAND=%~n0"\n'
        f'"{escaped_python}" "{escaped_shim}" %*\n'
    )


def _validate_launcher_path(launcher: Path) -> None:
    """Validate that *launcher* path can be used for a new .cmd file."""
    if launcher.exists() and not launcher.is_file():
        msg = f"{launcher} already exists and is not a file"
        raise FileExistsError(msg)


def _launcher_unlink_error(path: Path, exc: Exception) -> FileExistsError:
    """Return a descriptive exception when launcher removal exhausts retries."""
    msg = (
        f"Failed to remove existing launcher {path!r}: {exc}\n"
        "The file may be in use or locked. Please close any "
        "processes using it and try again."
    )
    return FileExistsError(msg)


def _create_windows_shim(directory: Path, name: str) -> Path:
    """Create a ``.cmd`` launcher for *name* that reuses :mod:`cmd_mox.shim`."""
    launcher = directory / f"{name}.cmd"
    _validate_launcher_path(launcher)
    retry_unlink(
        launcher,
        config=LAUNCHER_RETRY,
        logger=logger,
        exc_factory=_launcher_unlink_error,
    )
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


def _validate_shim_directory(directory: Path) -> None:
    """Validate that *directory* exists and is a directory."""
    if not directory.exists():
        msg = f"Shim directory does not exist: {directory}"
        raise FileNotFoundError(msg)
    if not directory.is_dir():
        msg = f"Shim directory is not a directory: {directory}"
        raise FileNotFoundError(msg)


def _ensure_shim_template_ready(shim_path: Path) -> None:
    """Validate *shim_path* exists and is executable."""
    if not shim_path.exists():
        msg = f"Shim template not found: {shim_path}"
        raise FileNotFoundError(msg)

    if not os.access(shim_path, os.X_OK):
        try:
            mode = shim_path.stat().st_mode | 0o111
            shim_path.chmod(mode)
        except OSError as exc:  # pragma: no cover - OS specific
            msg = f"Cannot make shim executable: {shim_path}"
            raise PermissionError(msg) from exc


def _create_shim_for_command(directory: Path, name: str) -> Path:
    """Create a platform-appropriate shim for *name* in *directory*."""
    _validate_command_name(name)
    if path_utils.IS_WINDOWS:
        return _create_windows_shim(directory, name)
    return _create_posix_symlink(directory, name)


def create_shim_symlinks(directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
    """Create shims for the given commands in *directory*.

    Parameters
    ----------
    directory:
        Directory where shims will be created. It must already exist.
    commands:
        Command names (e.g. "git", "curl") for which to create shims.
    """
    _validate_shim_directory(directory)
    _ensure_shim_template_ready(SHIM_PATH)
    command_list = list(commands)
    _validate_command_uniqueness(command_list)
    mapping: dict[str, Path] = {}
    for name in command_list:
        mapping[name] = _create_shim_for_command(directory, name)
    return mapping
