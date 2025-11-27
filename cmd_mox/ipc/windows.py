"""Windows-specific IPC helpers shared by client and server modules."""

from __future__ import annotations

import importlib
import hashlib
import os
import typing as t

from cmd_mox import _path_utils as path_utils

WINDOWS_PIPE_PREFIX: t.Final[str] = r"\\.\pipe\cmdmox-"
PIPE_CHUNK_SIZE: t.Final[int] = 64 * 1024
ERROR_BROKEN_PIPE: t.Final[int] = 109
ERROR_PIPE_BUSY: t.Final[int] = 231
ERROR_NO_DATA: t.Final[int] = 232
ERROR_MORE_DATA: t.Final[int] = 234
ERROR_PIPE_CONNECTED: t.Final[int] = 535
ERROR_OPERATION_ABORTED: t.Final[int] = 995
ERROR_FILE_NOT_FOUND: t.Final[int] = 2

if path_utils.IS_WINDOWS:  # pragma: win32-only
    try:
        pywintypes = importlib.import_module("pywintypes")
        win32file = importlib.import_module("win32file")
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        msg = "pywin32 is required for Windows named pipe support"
        raise RuntimeError(msg) from exc
else:  # pragma: no cover - satisfies type-checkers on non-Windows hosts
    pywintypes = t.cast("t.Any", None)
    win32file = t.cast("t.Any", None)


def derive_pipe_name(identifier: os.PathLike[str] | str) -> str:
    """Return a deterministic named pipe name for *identifier*.

    The helper hashes the identifier to ensure the resulting pipe name is both
    unique per shim directory and compatible with Windows' ``PIPE`` naming
    rules and maximum length constraints.
    """
    raw_value = os.fspath(identifier)
    digest = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()
    # Windows limits pipe names to 256 characters; a 32-character digest keeps
    # plenty of headroom for the prefix while remaining collision-resistant.
    return f"{WINDOWS_PIPE_PREFIX}{digest[:32]}"


def read_pipe_message(handle: object) -> bytes:
    """Read a full message from a named pipe handle.

    Windows named pipes in message mode can return ``ERROR_MORE_DATA`` when the
    caller-provided buffer is smaller than the pending message. This helper
    mirrors the chunked read loop used by both client and server, accumulating
    chunks until the pipe signals completion or is broken.
    """

    chunks: list[bytes] = []
    while True:
        try:
            hr, data = win32file.ReadFile(handle, PIPE_CHUNK_SIZE)  # type: ignore[union-attr]
        except pywintypes.error as exc:  # type: ignore[name-defined]
            if exc.winerror == ERROR_BROKEN_PIPE:
                break
            raise
        chunks.append(data)
        if hr == 0:
            break
        if hr != ERROR_MORE_DATA:
            break
    return b"".join(chunks)


def write_pipe_message(handle: object, payload: bytes) -> None:
    """Write *payload* to the pipe and flush it immediately."""

    win32file.WriteFile(handle, payload)  # type: ignore[union-attr]
    win32file.FlushFileBuffers(handle)  # type: ignore[union-attr]


__all__ = [
    "ERROR_BROKEN_PIPE",
    "ERROR_FILE_NOT_FOUND",
    "ERROR_MORE_DATA",
    "ERROR_NO_DATA",
    "ERROR_OPERATION_ABORTED",
    "ERROR_PIPE_BUSY",
    "ERROR_PIPE_CONNECTED",
    "PIPE_CHUNK_SIZE",
    "WINDOWS_PIPE_PREFIX",
    "derive_pipe_name",
    "read_pipe_message",
    "write_pipe_message",
]
