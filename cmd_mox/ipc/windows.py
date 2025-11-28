"""Windows-specific IPC helpers shared by client and server modules."""

from __future__ import annotations

import hashlib
import os
import typing as t

WINDOWS_PIPE_PREFIX: t.Final[str] = r"\\.\pipe\cmdmox-"
PIPE_CHUNK_SIZE: t.Final[int] = 64 * 1024
ERROR_BROKEN_PIPE: t.Final[int] = 109
ERROR_PIPE_BUSY: t.Final[int] = 231
ERROR_NO_DATA: t.Final[int] = 232
ERROR_MORE_DATA: t.Final[int] = 234
ERROR_PIPE_CONNECTED: t.Final[int] = 535
ERROR_OPERATION_ABORTED: t.Final[int] = 995
ERROR_FILE_NOT_FOUND: t.Final[int] = 2


class _PyWinError(Exception):
    """Minimal pywin32 error interface for type checking."""

    winerror: int


class _PyWinTypes(t.Protocol):
    """Interface capturing pywintypes' error attribute."""

    error: type[_PyWinError]


class _Win32File(t.Protocol):
    """Subset of win32file methods used by IPC helpers."""

    def ReadFile(  # noqa: N802 - mirrors pywin32 API casing
        self, handle: object, chunk_size: int
    ) -> tuple[int, bytes]: ...

    def WriteFile(  # noqa: N802 - mirrors pywin32 API casing
        self, handle: object, payload: bytes
    ) -> None: ...

    def FlushFileBuffers(  # noqa: N802 - mirrors pywin32 API casing
        self, handle: object
    ) -> None: ...


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


def read_pipe_message(
    handle: object,
    *,
    win32file: _Win32File,
    pywintypes: _PyWinTypes,
    chunk_size: int = PIPE_CHUNK_SIZE,
) -> bytes:
    """Read a complete message from a Windows named pipe *handle*.

    Windows delivers named pipe messages in chunks, reporting ``ERROR_MORE_DATA``
    while the message continues. We loop until the status indicates completion
    or the peer disappears (``ERROR_BROKEN_PIPE``), returning whatever data was
    received so callers can decide how to handle disconnects.
    """
    chunks: list[bytes] = []
    while True:
        try:
            hr, data = win32file.ReadFile(handle, chunk_size)
        except pywintypes.error as exc:
            if exc.winerror == ERROR_BROKEN_PIPE:
                break
            raise
        chunks.append(data)
        if hr == 0:
            break
        if hr != ERROR_MORE_DATA:
            break
    return b"".join(chunks)


def write_pipe_payload(
    handle: object, payload: bytes, *, win32file: _Win32File
) -> None:
    """Write *payload* to a named pipe *handle* and flush immediately."""
    win32file.WriteFile(handle, payload)
    win32file.FlushFileBuffers(handle)


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
    "write_pipe_payload",
]
