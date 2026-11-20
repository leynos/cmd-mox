"""Utilities for generating command shims."""

from __future__ import annotations

import logging
import os
import sys
import typing as typ
import warnings
from pathlib import Path

from cmd_mox import _path_utils as path_utils
from cmd_mox.environment import ensure_dir_exists
from cmd_mox.fs_retry import DEFAULT_UNLINK_RETRY, retry_unlink

if typ.TYPE_CHECKING:
    import collections.abc as cabc

SHIM_PATH = Path(__file__).with_name("shim.py").resolve()
logger = logging.getLogger(__name__)


def _escape_batch_literal(value: str) -> str:
    """Return *value* escaped for safe inclusion inside batch quotes.

    Parameters
    ----------
    value : str
        Literal to escape for a Windows batch command.

    Returns
    -------
    str
        Escaped batch literal.
    """
    escaped = value.replace("^", "^^").replace("%", "%%")
    return escaped.replace('"', '""')


def _validate_not_empty(name: str, error_msg: str) -> None:
    """Raise ``ValueError`` if *name* is empty.

    Raises
    ------
    ValueError
        If *name* is empty.
    """
    if not name:
        raise ValueError(error_msg)


def _validate_not_dot_directories(name: str, error_msg: str) -> None:
    """Disallow ``.`` and ``..`` because they change directory semantics.

    Raises
    ------
    ValueError
        If *name* is ``.`` or ``..``.
    """
    if name in {".", ".."}:
        raise ValueError(error_msg)


def _validate_no_path_separators(name: str, error_msg: str) -> None:
    """Ensure *name* contains no path separators for portability.

    Raises
    ------
    ValueError
        If *name* contains a host or alternate path separator.
    """
    separators = {"/", "\\", os.sep}
    if os.altsep:
        separators.add(os.altsep)
    if any(sep in name for sep in separators):
        raise ValueError(error_msg)


def _validate_no_nul_bytes(name: str, error_msg: str) -> None:
    """Reject names containing NUL bytes to avoid truncation.

    Raises
    ------
    ValueError
        If *name* contains a NUL byte.
    """
    if "\x00" in name:
        raise ValueError(error_msg)


def _validate_command_name(name: str) -> None:
    """Validate *name* as a safe command filename."""
    error_msg = f"Invalid command name: {name!r}"

    validators: list[cabc.Callable[[str, str], None]] = [
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
    """Ensure *commands* do not collide on case-insensitive filesystems.

    Raises
    ------
    ValueError
        If two command names resolve to the same comparison key.
    """
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
    """Return batch script contents that invoke ``shim.py`` on Windows.

    Parameters
    ----------
    python_executable : str
        Python executable used by the launcher.
    shim_path : pathlib.Path
        Path to the generic shim script.

    Returns
    -------
    str
        Complete Windows batch script.
    """
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


def _expected_windows_launcher_bytes() -> bytes:
    r"""Return the exact bytes written for a Windows ``.cmd`` launcher.

    ``write_text`` with ``newline="\r\n"`` expands every ``\n``, so building
    the expectation the same way keeps the health check byte-for-byte exact.

    Returns
    -------
    bytes
        UTF-8 launcher contents with CRLF delimiters and no BOM.
    """
    contents = _format_windows_launcher(sys.executable, SHIM_PATH)
    return contents.replace("\n", "\r\n").encode("utf-8")


def _validate_launcher_path(launcher: Path) -> None:
    """Validate that *launcher* can be used for a new ``.cmd`` file.

    Raises
    ------
    FileExistsError
        If an existing path is not a regular file.
    """
    if launcher.exists() and not launcher.is_file():
        msg = f"{launcher} already exists and is not a file"
        raise FileExistsError(msg)


def _launcher_unlink_error(path: Path, exc: Exception) -> FileExistsError:
    """Return an exception for an exhausted launcher-removal retry.

    Parameters
    ----------
    path : pathlib.Path
        Launcher that could not be removed.
    exc : Exception
        Final error reported by the removal attempt.

    Returns
    -------
    FileExistsError
        Descriptive error for the caller to raise.
    """
    msg = (
        f"Failed to remove existing launcher {path!r}: {exc}\n"
        "The file may be in use or locked. Please close any "
        "processes using it and try again."
    )
    return FileExistsError(msg)


def _create_windows_shim(directory: Path, name: str) -> Path:
    """Create a ``.cmd`` launcher for *name* using :mod:`cmd_mox.shim`.

    Parameters
    ----------
    directory : pathlib.Path
        Directory in which to create the launcher.
    name : str
        Command name represented by the launcher.

    Returns
    -------
    pathlib.Path
        Created launcher path.

    """
    launcher = path_utils.shim_launcher_path(directory, name)
    _validate_launcher_path(launcher)
    retry_unlink(
        launcher,
        config=DEFAULT_UNLINK_RETRY,
        logger=logger,
        exc_factory=_launcher_unlink_error,
    )
    _write_windows_launcher(launcher)
    return launcher


def _write_windows_launcher(launcher: Path) -> None:
    """Write *launcher*, removing a partial file when the write fails."""
    try:
        launcher.write_bytes(_expected_windows_launcher_bytes())
    except OSError:
        # The failed write can leave an empty or truncated launcher behind,
        # which a retry would then reject as a foreign entry.
        _discard_partial_launcher(launcher)
        raise


def _discard_partial_launcher(launcher: Path) -> None:
    """Remove a partially written launcher, logging a refused removal."""
    try:
        launcher.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Failed to remove partial launcher %s", launcher)


def _create_posix_symlink(directory: Path, name: str) -> Path:
    """Create a POSIX symlink for *name* pointing at :data:`SHIM_PATH`.

    Parameters
    ----------
    directory : pathlib.Path
        Directory in which to create the link.
    name : str
        Command name represented by the link.

    Returns
    -------
    pathlib.Path
        Created symlink path.

    Raises
    ------
    FileExistsError
        If an existing path is not a symlink.
    """
    link = path_utils.shim_launcher_path(directory, name)
    if os.path.lexists(link):
        if not link.is_symlink():
            msg = f"{link} already exists and is not a symlink"
            raise FileExistsError(msg)
        link.unlink()
    link.symlink_to(SHIM_PATH)
    return link


def _validate_shim_directory(directory: Path) -> None:
    """Validate that *directory* exists and is a directory."""
    ensure_dir_exists(directory, name="Shim directory")


def _ensure_shim_template_ready(shim_path: Path) -> None:
    """Validate *shim_path* exists and is executable.

    Raises
    ------
    FileNotFoundError
        If the template does not exist.
    PermissionError
        If the template cannot be made executable.
    """
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


def ensure_shim(directory: Path, name: str) -> Path:
    """Ensure *directory* holds a usable shim for *name*.

    The platform determines both the shim path and what counts as healthy. On
    POSIX the shim is a symlink to :data:`SHIM_PATH`; an existing live link is
    returned unchanged, while a broken link or missing entry is created or
    repaired. On Windows the shim is ``name.cmd``; a regular launcher whose
    bytes match cmd-mox's generator is returned unchanged, and every other
    entry is reported as a collision rather than overwritten.

    Parameters
    ----------
    directory : pathlib.Path
        Existing directory holding generated shims.
    name : str
        Command name represented by the shim.

    Returns
    -------
    pathlib.Path
        Path of the usable shim launcher or symlink.

    Raises
    ------
    ValueError
        If *name* is not a safe command filename.
    FileNotFoundError
        If *directory* or the shim template does not exist.
    PermissionError
        If the shim template cannot be made executable.
    FileExistsError
        If a non-cmd-mox entry already occupies the shim path.
    """  # ruff: ignore[docstring-extraneous-exception] - private validators raise these caller-visible failures
    _validate_shim_directory(directory)
    _ensure_shim_template_ready(SHIM_PATH)
    _validate_command_name(name)
    if path_utils.IS_WINDOWS:
        return _ensure_windows_launcher(directory, name)
    return _ensure_posix_symlink(directory, name)


def _ensure_posix_symlink(directory: Path, name: str) -> Path:
    """Return a link to the shim template, creating or repairing it if needed."""
    link = path_utils.shim_launcher_path(directory, name)
    if link.is_symlink() and link.exists():
        try:
            points_at_template = link.resolve() == SHIM_PATH
        except OSError:
            points_at_template = False
        if points_at_template:
            return link
    return _create_posix_symlink(directory, name)


def _ensure_windows_launcher(directory: Path, name: str) -> Path:
    """Return a matching launcher for *name*, creating it when absent."""
    launcher = path_utils.shim_launcher_path(directory, name)
    if not os.path.lexists(launcher):
        return _create_windows_shim(directory, name)
    _require_matching_launcher(launcher)
    return launcher


def _require_matching_launcher(launcher: Path) -> None:
    """Raise unless *launcher* is a regular file cmd-mox itself wrote."""
    if launcher.is_symlink() or not launcher.is_file():
        raise _launcher_collision_error(launcher)
    if _read_launcher_bytes(launcher) != _expected_windows_launcher_bytes():
        raise _launcher_collision_error(launcher)


def _read_launcher_bytes(launcher: Path) -> bytes:
    """Read *launcher*, reporting an unreadable entry as a collision."""
    try:
        return launcher.read_bytes()
    except OSError as exc:
        raise _launcher_collision_error(launcher) from exc


def _launcher_collision_error(launcher: Path) -> FileExistsError:
    """Return the error reporting a foreign entry at *launcher*."""
    return FileExistsError(f"{launcher} already exists and is not a cmd-mox launcher")


def _create_shim_for_command(directory: Path, name: str) -> Path:
    """Create a platform-appropriate shim for *name* in *directory*.

    Returns
    -------
    pathlib.Path
        Created launcher or symlink path.

    """
    _validate_command_name(name)
    if path_utils.IS_WINDOWS:
        return _create_windows_shim(directory, name)
    return _create_posix_symlink(directory, name)


def create_shims(directory: Path, commands: cabc.Iterable[str]) -> dict[str, Path]:
    """Create shims for the given commands in *directory*.

    Parameters
    ----------
    directory:
        Directory where shims will be created. It must already exist.
    commands:
        Command names (e.g. "git", "curl") for which to create shims.

    Returns
    -------
    dict[str, Path]
        The created shim path for each requested command.
    """
    _validate_shim_directory(directory)
    _ensure_shim_template_ready(SHIM_PATH)
    command_list = list(commands)
    _validate_command_uniqueness(command_list)
    mapping: dict[str, Path] = {}
    for name in command_list:
        mapping[name] = _create_shim_for_command(directory, name)
    return mapping


def create_shim_symlinks(
    directory: Path, commands: cabc.Iterable[str]
) -> dict[str, Path]:
    """Create shims for *commands* (deprecated alias for :func:`create_shims`).

    .. deprecated::
        Use :func:`create_shims` instead. The old name is inaccurate because
        Windows shims are ``.cmd`` batch launchers rather than symlinks.

    Parameters
    ----------
    directory : pathlib.Path
        Directory where shims will be created. It must already exist.
    commands : collections.abc.Iterable[str]
        Command names for which to create shims.

    Returns
    -------
    dict[str, Path]
        The created shim path for each requested command.

    Warns
    -----
    DeprecationWarning
        Always emitted; call :func:`create_shims` directly to avoid it.
    """
    warnings.warn(
        "create_shim_symlinks is deprecated; use create_shims instead",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_shims(directory, commands)
