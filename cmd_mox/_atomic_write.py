"""Internal helpers for durable, atomic JSON and byte writes.

The helpers here are private to CmdMox: they may change or move without
deprecation, and nothing in this module is exported from :mod:`cmd_mox`.

A write is a fixed sequence of named stages (:class:`AtomicWriteStage`). Each
stage is a private function that wraps one operating-system call, so a test can
replace a single stage inside this module instead of patching :mod:`os`
globally, and a failure names the step that actually failed.
"""

from __future__ import annotations

import collections.abc as cabc
import contextlib
import enum
import errno
import json
import logging
import os
import tempfile
import time
import typing as typ
from pathlib import Path

from . import _path_utils as path_utils
from .fs_retry import RetryConfig

_logger = logging.getLogger(__name__)

#: ``os.fchmod`` only exists on Windows from Python 3.13, so its absence is a
#: platform property rather than an error.
_FCHMOD = getattr(os, "fchmod", None)
_MAX_TEMPORARY_STEM_BYTES: typ.Final[int] = 64
_OWNER_READ_WRITE: typ.Final[int] = 0o600
_MAX_MODE: typ.Final[int] = 0o777
_WINDOWS_REPLACE_RETRY: typ.Final[RetryConfig] = RetryConfig(
    max_attempts=5, retry_delay=0.05
)
_UNSUPPORTED_DIRECTORY_SYNC_ERRNOS: typ.Final[frozenset[int]] = frozenset({
    errno.EINVAL,
    getattr(errno, "ENOTSUP", errno.EINVAL),
    getattr(errno, "EOPNOTSUPP", errno.EINVAL),
})

type JsonInput = (
    bool
    | int
    | float
    | str
    | cabc.Sequence[JsonInput]
    | cabc.Mapping[str, JsonInput]
    | None
)


class AtomicWriteStage(enum.StrEnum):
    """Named steps of the atomic write protocol, in execution order.

    Examples
    --------
    >>> AtomicWriteStage.REPLACE.value
    'replace'
    """

    SERIALIZE = "serialize"
    PREPARE_DIRECTORY = "prepare_directory"
    CREATE_TEMPORARY = "create_temporary"
    APPLY_MODE = "apply_mode"
    WRITE = "write"
    SYNC_FILE = "sync_file"
    CLOSE_TEMPORARY = "close_temporary"
    REPLACE = "replace"
    SYNC_DIRECTORY = "sync_directory"


POST_REPLACE_STAGES: typ.Final[frozenset[AtomicWriteStage]] = frozenset({
    AtomicWriteStage.SYNC_DIRECTORY
})
PRE_REPLACE_STAGES: typ.Final[frozenset[AtomicWriteStage]] = (
    frozenset(AtomicWriteStage) - POST_REPLACE_STAGES
)


class AtomicWriteError(OSError):
    """Raised when a durable atomic write cannot be completed.

    Parameters
    ----------
    path : pathlib.Path
        The target the caller asked to write.
    stage : AtomicWriteStage
        The protocol step that failed.
    cause : BaseException
        The original failure, also available as ``__cause__``.

    Attributes
    ----------
    path : pathlib.Path
        The target the caller asked to write.
    stage : AtomicWriteStage
        The protocol step that failed.
    target_replaced : bool
        ``True`` when the failure came after ``os.replace`` succeeded. The
        target then already holds the new document, and no rollback is
        attempted because durability, not visibility, is what failed.

    Examples
    --------
    >>> try:
    ...     atomic_write_json(Path("state.json"), {"version": 1})
    ... except AtomicWriteError as exc:
    ...     print(exc.stage.value)
    """

    path: Path
    stage: AtomicWriteStage
    target_replaced: bool

    def __init__(
        self, path: Path, stage: AtomicWriteStage, cause: BaseException
    ) -> None:
        """Build the message from the stage and the target's outcome."""
        self.path = path
        self.stage = stage
        self.target_replaced = stage in POST_REPLACE_STAGES
        outcome = (
            "the target was replaced"
            if self.target_replaced
            else "the previous file was left intact"
        )
        message = (
            f"Atomic write of {path} failed at stage {stage.value}: {cause}; {outcome}"
        )
        # Copying the cause's errno keeps `except OSError` handlers that check
        # for ENOSPC and friends working through the wrapper.
        error_number = cause.errno if isinstance(cause, OSError) else None
        if error_number is None:
            super().__init__(message)
        else:
            super().__init__(error_number, message, str(path))


def atomic_write_json(path: Path, payload: JsonInput, *, mode: int = 0o600) -> None:
    r"""Serialize *payload* and commit it to *path* atomically.

    Parameters
    ----------
    path : pathlib.Path
        Destination file. Missing parent directories are created.
    payload : JsonInput
        JSON-compatible data. Mappings keep their insertion order, so a
        load-then-save cycle is byte-stable.
    mode : int, optional
        POSIX permission bits for the destination. Defaults to ``0o600``,
        which is owner read and write.

    Raises
    ------
    AtomicWriteError
        If the payload cannot be serialized, or the write cannot complete.
        A stage of ``serialize`` means the payload was invalid; the
        filesystem was not touched.
    ValueError
        If *mode* is not an integer from ``0`` to ``0o777`` that grants owner
        read and write, or if *path* names no file.

    Examples
    --------
    >>> atomic_write_json(Path("state.json"), {"version": 1})
    >>> Path("state.json").read_text(encoding="utf-8")
    '{\n  "version": 1\n}\n'
    """  # ruff: ignore[docstring-extraneous-exception] - atomic_write_bytes and the validators raise these caller-visible failures.
    try:
        data = _serialize_json(payload)
    except Exception as exc:
        raise AtomicWriteError(path, AtomicWriteStage.SERIALIZE, exc) from exc
    atomic_write_bytes(path, data, mode=mode)


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o600) -> None:
    r"""Commit already-encoded *data* to *path* atomically.

    Parameters
    ----------
    path : pathlib.Path
        Destination file. Missing parent directories are created.
    data : bytes
        The exact bytes to install at *path*.
    mode : int, optional
        POSIX permission bits for the destination. Defaults to ``0o600``.

    Raises
    ------
    AtomicWriteError
        If the write cannot complete. Readers see either the previous file or
        the complete new one, never a mixture.
    ValueError
        If *mode* is not an integer from ``0`` to ``0o777`` that grants owner
        read and write, or if *path* names no file.

    Examples
    --------
    >>> atomic_write_bytes(Path("state.bin"), b"payload\n")
    >>> Path("state.bin").read_bytes()
    b'payload\n'
    """  # ruff: ignore[docstring-extraneous-exception] - the commit protocol and the validators raise these caller-visible failures.
    _validate_mode(mode)
    _validate_target_name(path)
    _commit(path, data, mode)


def _commit(path: Path, data: bytes, mode: int) -> None:
    """Run the commit protocol, attributing any failure to its stage."""
    stage = AtomicWriteStage.PREPARE_DIRECTORY
    fd: int | None = None
    temporary: Path | None = None
    try:
        _make_parent(path)
        stage = AtomicWriteStage.CREATE_TEMPORARY
        fd, temporary = _create_temporary(path)
        stage = AtomicWriteStage.APPLY_MODE
        _apply_mode(fd, temporary, mode)
        stage = AtomicWriteStage.WRITE
        _write_all(fd, data)
        stage = AtomicWriteStage.SYNC_FILE
        _fsync_descriptor(fd)
        stage = AtomicWriteStage.CLOSE_TEMPORARY
        # Ownership leaves the protocol before the close is attempted: on
        # Linux a failed close still releases the descriptor, so a retry in
        # cleanup could close an unrelated descriptor another thread reused.
        closing = fd
        fd = None
        _close_descriptor(closing)
        stage = AtomicWriteStage.REPLACE
        _replace(temporary, path)
        stage = AtomicWriteStage.SYNC_DIRECTORY
        _fsync_directory(path.parent)
    except BaseException as exc:
        # Cleanup is gated on the stage rather than on code position: once the
        # rename succeeded there is nothing left to remove, and the failure
        # must be reported as "replaced".
        notes = (
            _cleanup_after_failure(fd, temporary) if stage in PRE_REPLACE_STAGES else []
        )
        if isinstance(exc, Exception):
            error = AtomicWriteError(path, stage, exc)
            for note in notes:
                error.add_note(note)
            raise error from exc
        for note in notes:
            exc.add_note(note)
        raise


def _serialize_json(payload: JsonInput) -> bytes:
    """Serialize *payload* as two-space-indented JSON plus a newline."""
    serialized = json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False)
    return (serialized + "\n").encode("utf-8")


def _make_parent(path: Path) -> None:
    """Create the target's parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)


def _temporary_prefix(name: str) -> str:
    """Build a temporary-file prefix that fits within the name limit."""
    encoded = name.encode("utf-8", errors="surrogatepass")[:_MAX_TEMPORARY_STEM_BYTES]
    while True:
        try:
            stem = encoded.decode("utf-8", errors="surrogatepass")
        except UnicodeDecodeError:
            # The byte limit split a multibyte character; drop the fragment.
            encoded = encoded[:-1]
        else:
            return f".{stem}."


def _create_temporary(path: Path) -> tuple[int, Path]:
    """Create a unique sibling temporary file for *path*."""
    descriptor, name = tempfile.mkstemp(
        dir=path.parent, prefix=_temporary_prefix(path.name), suffix=".tmp"
    )
    return descriptor, Path(name)


def _apply_mode(fd: int, temporary: Path, mode: int) -> None:
    """Apply *mode* to the temporary file before any content is written."""
    if _FCHMOD is None:
        temporary.chmod(mode)
    else:
        _FCHMOD(fd, mode)


def _write_once(fd: int, buffer: memoryview) -> int:
    """Write as much of *buffer* as the operating system accepts."""
    return os.write(fd, buffer)


def _write_all(fd: int, data: bytes) -> None:
    """Write every byte of *data* to *fd* without a user-space buffer."""
    remaining = memoryview(data)
    while remaining:
        written = _write_once(fd, remaining)
        remaining = remaining[written:]


def _fsync_descriptor(fd: int) -> None:
    """Synchronize one descriptor to stable storage."""
    os.fsync(fd)


def _discard_temporary(temporary: Path) -> None:
    """Remove *temporary* if it still exists."""
    temporary.unlink(missing_ok=True)


def _cleanup_after_failure(fd: int | None, temporary: Path | None) -> list[str]:
    """Release the temporary file, noting anything that could not be undone."""
    notes: list[str] = []
    if fd is not None:
        notes.extend(_close_quietly(fd))
    if temporary is not None:
        notes.extend(_remove_quietly(temporary))
    return notes


def _close_quietly(fd: int) -> list[str]:
    """Close *fd*, returning a note when it cannot be closed."""
    # Close through ``os.close`` rather than the ``_close_descriptor`` seam:
    # cleanup must still release the descriptor when that seam is the one that
    # failed.
    try:
        os.close(fd)
    except OSError as exc:
        return [f"Failed to close the temporary file: {exc}"]
    return []


def _remove_quietly(temporary: Path) -> list[str]:
    """Remove *temporary*, returning a note when it cannot be removed."""
    try:
        _discard_temporary(temporary)
    except OSError as exc:
        return [f"Failed to remove temporary file {temporary}: {exc}"]
    return []


def _os_replace(temporary: Path, path: Path) -> None:
    """Rename *temporary* over *path*."""
    # This is the raw rename, kept as a seam so tests can inject a sharing
    # violation; Path.replace cannot be patched without touching pathlib
    # globally, and a wrapped Path call is a trivial alias.
    os.replace(temporary, path)  # ruff: ignore[os-replace] - module-local seam patched by tests


def _replace(temporary: Path, path: Path) -> None:
    """Replace *path* with *temporary*, retrying Windows sharing violations."""
    if not path_utils.IS_WINDOWS:
        _os_replace(temporary, path)
        return
    _replace_with_retry(temporary, path, _WINDOWS_REPLACE_RETRY)


def _replace_with_retry(temporary: Path, path: Path, config: RetryConfig) -> None:
    """Retry the replacement while another process holds the target open."""
    # All but the last attempt are retried; the last one propagates so the
    # caller sees the real PermissionError. Windows only: antivirus scanners,
    # indexers, and parallel test workers open files without delete sharing.
    for _ in range(config.max_attempts - 1):
        try:
            _os_replace(temporary, path)
        except PermissionError:
            time.sleep(config.retry_delay)
        else:
            return
    _os_replace(temporary, path)


def _fsync_directory(directory: Path) -> None:
    """Synchronize *directory*'s entries where the platform supports it."""
    if path_utils.IS_WINDOWS:
        # Windows has no portable directory-descriptor flush.
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except PermissionError:
        # A write-only parent directory cannot be read back; that is a
        # capability limit, not a failed write.
        _log_directory_sync_skipped(directory)
        return
    try:
        _fsync_descriptor(descriptor)
    except OSError as exc:
        if exc.errno not in _UNSUPPORTED_DIRECTORY_SYNC_ERRNOS:
            raise
        _log_directory_sync_skipped(directory)
    finally:
        # A close error must not replace the synchronization error that is
        # already propagating; the descriptor is released on Linux either way.
        with contextlib.suppress(OSError):
            _close_descriptor(descriptor)


def _close_descriptor(fd: int) -> None:
    """Close a directory descriptor."""
    os.close(fd)


def _log_directory_sync_skipped(directory: Path) -> None:
    """Record that directory synchronization is unsupported here."""
    _logger.debug(
        "Directory synchronization is unsupported for %s; skipping", directory
    )


def _validate_mode(mode: int) -> None:
    """Reject modes this helper cannot apply safely."""
    if isinstance(mode, bool) or not isinstance(mode, int):
        message = f"mode must be an int, got {type(mode).__name__}"
        # One error type for every invalid mode keeps the caller contract
        # simple; the message says which rule was broken.
        raise ValueError(message)  # ruff: ignore[type-check-without-type-error] - a bad mode value has one documented error type
    if not 0 <= mode <= _MAX_MODE:
        message = f"mode must be between 0 and 0o777, got {oct(mode)}"
        raise ValueError(message)
    if mode & _OWNER_READ_WRITE != _OWNER_READ_WRITE:
        # A mode without owner write makes the file read-only on Windows, so
        # the next replacement onto it fails; without owner read the writer
        # cannot read back what it just wrote.
        message = f"mode must grant owner read and write (0o600), got {oct(mode)}"
        raise ValueError(message)


def _validate_target_name(path: Path) -> None:
    """Reject a target that names no file."""
    if not path.name:
        message = f"target path must name a file, not a directory: {path!s}"
        raise ValueError(message)
