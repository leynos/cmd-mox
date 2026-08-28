"""Windows-specific IPC helpers shared by client and server modules."""

from __future__ import annotations

import dataclasses as dc
import functools
import hashlib
import logging
import os
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Reads one chunk of *chunk_size* bytes, returning ``(status, data)`` exactly
#: as :func:`win32file.ReadFile` would.
type PipeChunkReader = cabc.Callable[[int], tuple[int, bytes]]

#: One chunk read outcome: ``(status, data)``, or ``None`` once the peer has
#: disconnected. Private to :func:`_read_pipe_chunk`; not part of the public
#: surface.
type PipeReadResult = tuple[int, bytes] | None

WINDOWS_PIPE_PREFIX: typ.Final[str] = r"\\.\pipe\cmdmox-"
PIPE_CHUNK_SIZE: typ.Final[int] = 64 * 1024
# Largest IPC message the transport will buffer for a single client. A real
# invocation payload (command name, argv, stdin, and environment) is kilobytes
# at worst, so 8 MiB leaves several orders of magnitude of headroom while still
# bounding the memory one misbehaving or hostile peer can pin. The limit is
# deliberately conservative: it is cheap to raise, whereas an unbounded reader
# lets a single client exhaust the server process.
MAX_MESSAGE_SIZE: typ.Final[int] = 8 * 1024 * 1024
ERROR_BROKEN_PIPE: typ.Final[int] = 109
ERROR_PIPE_BUSY: typ.Final[int] = 231
ERROR_NO_DATA: typ.Final[int] = 232
ERROR_MORE_DATA: typ.Final[int] = 234
ERROR_PIPE_CONNECTED: typ.Final[int] = 535
ERROR_OPERATION_ABORTED: typ.Final[int] = 995
ERROR_IO_PENDING: typ.Final[int] = 997
ERROR_FILE_NOT_FOUND: typ.Final[int] = 2

logger = logging.getLogger(__name__)


class PipeMessageTooLargeError(RuntimeError):
    """Raised when a named-pipe message exceeds the configured size limit.

    The exception carries byte counts only. Message content is dropped as soon
    as the limit is breached and is never retained, logged, or embedded in the
    exception, so this error is safe to report through the bounded
    observability seam.

    Attributes
    ----------
    received:
        Number of bytes read when the limit was breached.
    limit:
        Configured maximum message size in bytes.
    """

    def __init__(self, *, received: int, limit: int) -> None:
        msg = f"named pipe message exceeded the {limit}-byte limit"
        super().__init__(msg)
        self.received = received
        self.limit = limit


class _PyWinError(Exception):
    """Minimal pywin32 error interface for type checking."""

    winerror: int


class _PyWinTypes(typ.Protocol):
    """Interface capturing pywintypes' error attribute."""

    error: type[_PyWinError]


class _Win32File(typ.Protocol):
    """Subset of win32file methods used by IPC helpers."""

    def ReadFile(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self, handle: object, chunk_size: int
    ) -> tuple[int, bytes]: ...

    def WriteFile(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self, handle: object, payload: bytes
    ) -> None: ...

    def FlushFileBuffers(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self, handle: object
    ) -> None: ...

    def CloseHandle(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self, handle: object
    ) -> None: ...

    def AllocateReadBuffer(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self, size: int
    ) -> object: ...

    def GetOverlappedResult(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self,
        handle: object,
        overlapped: object,
        wait: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - pywin32 takes the wait flag positionally
    ) -> int: ...

    def CancelIoEx(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self, handle: object, overlapped: object
    ) -> None: ...


Win32FileProtocol = _Win32File
PyWinTypesProtocol = _PyWinTypes
PyWinErrorProtocol = _PyWinError


def derive_pipe_name(identifier: os.PathLike[str] | str) -> str:
    """Return a deterministic named pipe name for *identifier*.

    The helper hashes the identifier to ensure the resulting pipe name is both
    unique per shim directory and compatible with Windows' ``PIPE`` naming
    rules and maximum length constraints.

    Returns
    -------
    str
        The deterministic Windows named-pipe path.
    """
    raw_value = os.fspath(identifier)
    digest = hashlib.sha256(raw_value.encode("utf-8")).hexdigest()
    # Windows limits pipe names to 256 characters; a 32-character digest keeps
    # plenty of headroom for the prefix while remaining collision-resistant.
    return f"{WINDOWS_PIPE_PREFIX}{digest[:32]}"


@dc.dataclass(frozen=True, slots=True)
class PipeReadOptions:
    """Tunables for :func:`read_pipe_message`.

    Bundling these keeps the reader's call surface small while letting the
    server supply a cancellable reader and the shared size limit.

    Parameters
    ----------
    chunk_size:
        Bytes requested per ``ReadFile`` call.
    max_bytes:
        Maximum total message size. Exceeding it raises
        :class:`PipeMessageTooLargeError`.
    read_chunk:
        Optional replacement for the default synchronous ``ReadFile`` call,
        used by the server to enforce a per-client deadline.
    """

    chunk_size: int = PIPE_CHUNK_SIZE
    max_bytes: int = MAX_MESSAGE_SIZE
    read_chunk: PipeChunkReader | None = None


def _continue_reading(status: int) -> bool:
    """Return whether the message continues past the chunk just read.

    Returns
    -------
    bool
        ``True`` when Windows reported ``ERROR_MORE_DATA``; ``False`` on
        completion or on an unexpected status, which is logged.
    """
    if status == 0:
        return False
    if status == ERROR_MORE_DATA:
        return True
    logger.warning("Unexpected ReadFile status: %s; returning partial data", status)
    return False


def _read_pipe_chunk(
    read_chunk: PipeChunkReader,
    chunk_size: int,
    *,
    pywintypes: PyWinTypesProtocol,
) -> PipeReadResult:
    """Read one pipe chunk and handle peer disconnection.

    Returns
    -------
    PipeReadResult
        The ``(status, data)`` pair, or ``None`` when the peer disconnected.
    """
    try:
        return read_chunk(chunk_size)
    except pywintypes.error as exc:
        if exc.winerror == ERROR_BROKEN_PIPE:
            return None
        raise


def read_pipe_message(
    handle: object,
    *,
    win32file: Win32FileProtocol,
    pywintypes: PyWinTypesProtocol,
    options: PipeReadOptions | None = None,
) -> bytes:
    """Read a complete, size-bounded message from a named pipe *handle*.

    Windows delivers named pipe messages in chunks, reporting ``ERROR_MORE_DATA``
    while the message continues. We loop until the status indicates completion
    or the peer disappears (``ERROR_BROKEN_PIPE``), returning whatever data was
    received so callers can decide how to handle disconnects.

    Reading stops as soon as the accumulated message would exceed
    ``options.max_bytes``. The buffered prefix is discarded at that point, so an
    oversized message is never fully retained in memory.

    Returns
    -------
    bytes
        The complete message, or the received prefix after a peer disconnect.

    Raises
    ------
    PipeMessageTooLargeError
        If the peer sends more than ``options.max_bytes`` bytes.
    """
    options = options or PipeReadOptions()
    read_chunk = options.read_chunk or functools.partial(win32file.ReadFile, handle)
    chunks: list[bytes] = []
    received = 0
    while True:
        result = _read_pipe_chunk(read_chunk, options.chunk_size, pywintypes=pywintypes)
        if result is None:
            break
        status, data = result
        received += len(data)
        if received > options.max_bytes:
            # Drop the buffered prefix: the message is already unusable, and
            # retaining it would defeat the point of the limit.
            chunks.clear()
            raise PipeMessageTooLargeError(received=received, limit=options.max_bytes)
        chunks.append(data)
        if not _continue_reading(status):
            break
    return b"".join(chunks)


def write_pipe_payload(
    handle: object, payload: bytes, *, win32file: Win32FileProtocol
) -> None:
    """Write *payload* to a named pipe *handle* and flush immediately."""
    win32file.WriteFile(handle, payload)
    win32file.FlushFileBuffers(handle)


__all__ = sorted([
    "ERROR_BROKEN_PIPE",
    "ERROR_FILE_NOT_FOUND",
    "ERROR_IO_PENDING",
    "ERROR_MORE_DATA",
    "ERROR_NO_DATA",
    "ERROR_OPERATION_ABORTED",
    "ERROR_PIPE_BUSY",
    "ERROR_PIPE_CONNECTED",
    "MAX_MESSAGE_SIZE",
    "PIPE_CHUNK_SIZE",
    "PipeChunkReader",
    "PipeMessageTooLargeError",
    "PipeReadOptions",
    "PyWinErrorProtocol",
    "PyWinTypesProtocol",
    "WINDOWS_PIPE_PREFIX",
    "Win32FileProtocol",
    "derive_pipe_name",
    "read_pipe_message",
    "write_pipe_payload",
])
