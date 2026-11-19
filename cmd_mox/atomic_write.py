"""Atomic UTF-8 file persistence for fixtures and small JSON documents.

A fixture write must never leave a truncated file behind.  The helpers here
write a sibling temporary file, flush and ``fsync`` it, then install it with
:func:`os.replace`, so a reader observes either the previous complete file or
the new complete file.  They are internal support code shared by record mode
and, later, file-backed fake state.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
import typing as typ
from pathlib import Path

from ._path_utils import IS_WINDOWS

# Owner read/write only.  Fixtures and fake state can contain values derived
# from the environment or command output, so other users on a shared host
# should not be able to read them.
DEFAULT_FILE_MODE: typ.Final[int] = 0o600

# Serialize with a plain newline on every platform so committed fixtures have
# identical bytes and stable diffs.
_NEWLINE: typ.Final[str] = "\n"

# ``mkstemp`` adds eight random characters plus the suffix to the prefix.  Cap
# the UTF-8 bytes copied from the destination name so a fixture whose own name
# is near the filesystem's 255-byte limit still gets a creatable temporary name.
_MAX_TEMP_STEM_BYTES: typ.Final[int] = 64


class AtomicWriteError(OSError):
    """Raised when a file cannot be replaced atomically.

    Parameters
    ----------
    message : str
        Description of the failed operation, including the destination.
    path : pathlib.Path
        Destination that could not be written.

    Notes
    -----
    The original operating-system error is available through
    :attr:`BaseException.__cause__`.  When replacement never happened, the
    previous destination file is still complete.
    """

    def __init__(self, message: str, *, path: Path) -> None:
        """Record the destination alongside the operating-system message."""
        super().__init__(message)
        self.path = path


def _restrict_permissions(descriptor: int, mode: int) -> None:
    """Apply *mode* to *descriptor* where the platform supports it."""
    # Windows has no ``fchmod``; the temporary file keeps the access control
    # list inherited from its directory, so POSIX mode bits are best-effort.
    if hasattr(os, "fchmod"):
        os.fchmod(descriptor, mode)


def _write_and_sync(descriptor: int, content: str, mode: int) -> None:
    """Write *content* as UTF-8, flush it, and force it to disk."""
    with os.fdopen(descriptor, "w", encoding="utf-8", newline=_NEWLINE) as stream:
        _restrict_permissions(stream.fileno(), mode)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def _sync_parent_directory(directory: Path) -> None:
    """Flush *directory* so the rename survives a crash on POSIX.

    Windows does not expose directory handles to :func:`os.fsync` through the
    standard library, so the directory sync is skipped there explicitly.
    """
    if IS_WINDOWS:
        return

    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _temporary_prefix(target: Path) -> str:
    """Return a temporary-file prefix that stays within filename limits."""
    # Truncating encoded bytes can split a multi-byte character; dropping the
    # partial tail keeps the prefix valid UTF-8.
    stem = target.name.encode("utf-8")[:_MAX_TEMP_STEM_BYTES].decode("utf-8", "ignore")
    return f".{stem}."


def _sync_directory_chain(directory: Path) -> None:
    """Flush *directory* and its ancestors so new entries survive a crash.

    ``directory`` is flushed strictly because a failure there leaves the
    rename itself uncertain.  Ancestors are flushed best-effort: a retry may
    reuse directories an earlier failed attempt created, so they still need
    flushing, but an ancestor that cannot be opened must not fail a write
    whose own directory is already durable.
    """
    _sync_parent_directory(directory)
    current = directory
    while True:
        parent = current.parent
        if parent == current:
            return
        with contextlib.suppress(OSError):
            _sync_parent_directory(parent)
        current = parent


def atomic_write_text(
    path: Path,
    content: str,
    *,
    mode: int = DEFAULT_FILE_MODE,
) -> None:
    """Write *content* to *path* as UTF-8, replacing it atomically.

    The temporary file lives in the destination directory so :func:`os.replace`
    stays on one filesystem and is atomic on POSIX.  When *path* is a symbolic
    link, the link itself is replaced; the file it pointed at is left intact.

    Parameters
    ----------
    path : pathlib.Path
        Destination file.  Parent directories are created when missing.
    content : str
        Text to persist.  It is encoded as UTF-8 with LF line endings.
    mode : int
        Permission bits applied where the platform supports them.  The
        default is owner read/write only.

    Raises
    ------
    AtomicWriteError
        If creating the temporary file, writing, syncing, replacing, or
        syncing the parent directory fails.  The destination path is attached
        as ``path`` and the original error is chained.
    """
    target = Path(path)
    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=target.parent,
            prefix=_temporary_prefix(target),
            suffix=".tmp",
        )
        temporary = Path(temporary_name)
        try:
            _write_and_sync(descriptor, content, mode)
            temporary.replace(target)
            temporary = None
        finally:
            # A cleanup failure must not hide the original write failure.
            if temporary is not None:
                with contextlib.suppress(OSError):
                    temporary.unlink(missing_ok=True)
        # Flushing a directory does not persist its own entry in its parent,
        # so the chain is walked from the innermost directory outwards.
        _sync_directory_chain(target.parent)
    except OSError as exc:
        msg = f"Could not atomically write {target}"
        raise AtomicWriteError(msg, path=target) from exc


def atomic_write_json(
    path: Path,
    payload: object,
    *,
    mode: int = DEFAULT_FILE_MODE,
) -> None:
    """Serialize *payload* as indented JSON and write it atomically.

    Parameters
    ----------
    path : pathlib.Path
        Destination file.
    payload : object
        JSON-serializable value.  Mapping key order follows the payload's
        insertion order; keys are not sorted.
    mode : int
        Permission bits applied where the platform supports them.

    Raises
    ------
    AtomicWriteError
        If serialization fails or the file cannot be written atomically.
    """
    target = Path(path)
    try:
        text = json.dumps(payload, indent=2) + _NEWLINE
    except (TypeError, ValueError) as exc:
        msg = f"Could not serialize JSON for {target}"
        raise AtomicWriteError(msg, path=target) from exc
    atomic_write_text(target, text, mode=mode)
