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


@dc.dataclass(frozen=True, slots=True)
class NamedPipeCreateSpec:
    """The arguments one ``CreateNamedPipe`` instance needs.

    ``win32pipe.CreateNamedPipe`` takes eight positional arguments, which is
    more than a call site can pass without the reader having to hold the
    Win32 signature in their head. Grouping them into a value object keeps the
    port signature small and lets a test name the field it cares about instead
    of counting positions.

    Attributes
    ----------
    name:
        Fully qualified pipe name, usually from :func:`derive_pipe_name`.
    open_mode:
        Duplex access plus the overlapped-I/O flag.
    pipe_mode:
        Message-mode flags.
    max_instances:
        Upper bound on simultaneous instances.
    out_buffer_size:
        Size of the server-to-client buffer.
    in_buffer_size:
        Size of the client-to-server buffer.
    default_timeout:
        Default timeout in milliseconds for blocking pipe operations.
    security_attributes:
        Optional ``SECURITY_ATTRIBUTES``, or ``None`` for the default.
    """

    name: str
    open_mode: int
    pipe_mode: int
    max_instances: int
    out_buffer_size: int
    in_buffer_size: int
    default_timeout: int
    security_attributes: object


@dc.dataclass(frozen=True, slots=True)
class PipeFileOpenSpec:
    """The arguments one ``CreateFile`` call needs.

    The transport uses ``CreateFile`` for a single purpose: opening the pipe
    once so its accept loop wakes up. Naming the fields keeps that call site
    readable and stops the seven-positional-argument shape of ``CreateFile``
    from spreading through the port.

    Attributes
    ----------
    file_name:
        Path to the object being opened.
    desired_access:
        Requested access rights.
    share_mode:
        Sharing flags, usually ``0`` for an exclusive open.
    security_attributes:
        Optional ``SECURITY_ATTRIBUTES``, or ``None`` for the default.
    creation_disposition:
        Whether to create or open an existing object.
    flags_and_attributes:
        Additional file flags.
    template_file:
        Optional template handle, or ``None``.
    """

    file_name: str
    desired_access: int
    share_mode: int
    security_attributes: object
    creation_disposition: int
    flags_and_attributes: int
    template_file: object


class NamedPipeBackend(typ.Protocol):
    """Narrow port over the Win32 named-pipe operations the transport needs.

    :class:`~cmd_mox.ipc.named_pipe._NamedPipeState` owns pipe lifecycle,
    cancellation, and bounded reads, but it must not reach for ``pywin32``
    module globals. This port is that seam: the Windows adapter implements it
    over the real modules, and the transport tests implement it over
    scriptable fakes.

    Handles, buffers, and overlapped structures stay opaque ``object`` values.
    They are created by this port and only ever passed back to it, so naming
    the concrete Win32 types here would add coupling without adding safety.
    """

    #: ``win32file`` view used by the shared read/write helpers.
    win32file: Win32FileProtocol
    #: ``pywintypes`` view used by the shared read/write helpers.
    pywintypes: PyWinTypesProtocol
    #: Exception type raised by every failing Win32 call.
    error: type[PyWinErrorProtocol]

    #: ``win32event`` timeout sentinel meaning "wait forever".
    INFINITE: int
    #: ``win32event`` result reporting that the first handle was signalled.
    WAIT_OBJECT_0: int
    #: ``win32event`` result reporting that the wait timed out.
    WAIT_TIMEOUT: int

    #: ``win32pipe`` access mode granting both directions.
    PIPE_ACCESS_DUPLEX: int
    #: ``win32pipe`` mode delivering whole messages.
    PIPE_TYPE_MESSAGE: int
    #: ``win32pipe`` mode reading whole messages.
    PIPE_READMODE_MESSAGE: int
    #: ``win32pipe`` mode making blocking calls wait rather than fail.
    PIPE_WAIT: int
    #: ``win32pipe`` sentinel allowing an unbounded instance count.
    PIPE_UNLIMITED_INSTANCES: int

    #: ``win32file`` flag opening the pipe for overlapped I/O.
    FILE_FLAG_OVERLAPPED: int
    #: ``win32file`` access right granting reads.
    GENERIC_READ: int
    #: ``win32file`` access right granting writes.
    GENERIC_WRITE: int
    #: ``win32file`` creation disposition opening an existing object.
    OPEN_EXISTING: int

    def create_event(self) -> object:
        """Create the unnamed, manual-reset event the transport waits on.

        Returns
        -------
        object
            The new event handle.
        """
        ...

    def set_event(self, handle: object) -> None:
        """Signal *handle* so every waiter wakes.

        Parameters
        ----------
        handle:
            The event handle to signal.
        """
        ...

    def wait(self, handles: cabc.Sequence[object], timeout_ms: int) -> int:
        """Wait until any of *handles* is signalled, or *timeout_ms* expires.

        Parameters
        ----------
        handles:
            The event handles to wait on.
        timeout_ms:
            Milliseconds to wait, or ``INFINITE`` to block until signalled.

        Returns
        -------
        int
            The Win32 wait result: ``WAIT_OBJECT_0`` plus the index of the
            signalled handle, ``WAIT_TIMEOUT``, or another status.
        """
        ...

    def create_overlapped(self) -> tuple[object, object]:
        """Build an ``OVERLAPPED`` with a dedicated manual-reset event.

        Returns
        -------
        tuple[object, object]
            The overlapped structure and its event handle. The caller owns the
            event and must close it.
        """
        ...

    def get_overlapped_result(self, handle: object, overlapped: object) -> int:
        """Wait for *overlapped* to complete and return the bytes transferred.

        Parameters
        ----------
        handle:
            The handle the overlapped operation was issued on.
        overlapped:
            The overlapped structure tracking that operation.

        Returns
        -------
        int
            The number of bytes the completed operation transferred.
        """
        ...

    def cancel_io(self, handle: object, overlapped: object) -> None:
        """Request cancellation of the operation *overlapped* describes.

        Parameters
        ----------
        handle:
            The handle the overlapped operation was issued on.
        overlapped:
            The overlapped structure identifying the operation to cancel.
        """
        ...

    def create_named_pipe(self, spec: NamedPipeCreateSpec) -> object:
        """Create one named-pipe instance described by *spec*.

        Parameters
        ----------
        spec:
            The creation arguments, already grouped as a value object so the
            eight positional ``CreateNamedPipe`` arguments stay named.

        Returns
        -------
        object
            The new pipe instance handle.
        """
        ...

    def connect_named_pipe(self, handle: object, overlapped: object) -> int:
        """Start connecting *handle* to a client and report the initial status.

        Parameters
        ----------
        handle:
            The pipe instance awaiting a client.
        overlapped:
            The overlapped structure tracking the connection attempt.

        Returns
        -------
        int
            ``ERROR_IO_PENDING`` when the connect is asynchronous, otherwise
            the status reported synchronously.
        """
        ...

    def disconnect_named_pipe(self, handle: object) -> None:
        """Disconnect *handle* from its client.

        Parameters
        ----------
        handle:
            The connected pipe instance to detach.
        """
        ...

    def read_file(
        self, handle: object, buffer: cabc.Buffer, overlapped: object
    ) -> tuple[int, cabc.Buffer]:
        """Start an overlapped read and report the immediate status.

        Parameters
        ----------
        handle:
            The connected pipe instance to read from.
        buffer:
            The writable buffer the kernel fills with the received bytes.
        overlapped:
            The overlapped structure tracking the read.

        Returns
        -------
        tuple[int, cabc.Buffer]
            The immediate status and the buffer filled by the read.
        """
        ...

    def allocate_read_buffer(self, size: int) -> cabc.Buffer:
        """Allocate a writable buffer of *size* bytes for an overlapped read.

        Parameters
        ----------
        size:
            Buffer capacity in bytes, normally one pipe chunk.

        Returns
        -------
        collections.abc.Buffer
            A zeroed buffer the kernel can write into.
        """
        ...

    def create_file(self, spec: PipeFileOpenSpec) -> object:
        """Open the pipe described by *spec*, used only to wake the accept loop.

        Parameters
        ----------
        spec:
            The open arguments, already grouped as a value object so the seven
            positional ``CreateFile`` arguments stay named.

        Returns
        -------
        object
            The opened handle.
        """
        ...

    def close_handle(self, handle: object) -> None:
        """Release *handle*.

        Parameters
        ----------
        handle:
            The handle to release. Handles only ever come back to this port
            from its own creation methods.
        """
        ...


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

    def __post_init__(self) -> None:
        """Reject read tunables that cannot describe a usable read.

        A non-positive ``chunk_size`` is the more dangerous of the two: a
        zero-byte read reports ``ERROR_MORE_DATA`` with no data, so the
        received count never grows and :func:`read_pipe_message` spins forever.
        A non-positive ``max_bytes`` does not hang — it fails fast with
        :class:`PipeMessageTooLargeError` on the first chunk — but it is
        equally a misconfiguration, so both are rejected up front.

        Raises
        ------
        ValueError
            If ``chunk_size`` or ``max_bytes`` is not strictly positive.
        """
        if self.chunk_size <= 0:
            msg = f"chunk_size must be positive, got {self.chunk_size}"
            raise ValueError(msg)
        if self.max_bytes <= 0:
            msg = f"max_bytes must be positive, got {self.max_bytes}"
            raise ValueError(msg)


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
    "NamedPipeBackend",
    "NamedPipeCreateSpec",
    "PIPE_CHUNK_SIZE",
    "PipeChunkReader",
    "PipeFileOpenSpec",
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
