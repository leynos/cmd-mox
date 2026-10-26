"""Capture a command shim's stdin under one monotonic deadline.

The shim must never wait on stdin for longer than ``CMOX_IPC_TIMEOUT``. How
that can be enforced depends on what the host exposes:

``poll``
    A POSIX descriptor that both ``select.poll`` and ``select.select`` accept.
    Readiness is awaited with the remaining deadline, so no read ever blocks.
``regular``
    A POSIX descriptor backed by a regular file, such as a redirected input
    file. Readiness polling cannot bound a file read, so the read runs on a
    daemon worker.
``win32``
    A Windows handle reachable through ``msvcrt.get_osfhandle``. ``ReadFile``
    blocks until data or EOF, so it runs on a daemon worker that is interrupted
    with ``CancelSynchronousIo`` when the deadline expires.
``fallback``
    Anything else: a stream without a descriptor, a descriptor neither poller
    accepts, or a Windows stream whose handle cannot be resolved. A daemon
    worker performs the plain text read and is tracked until it finishes.

Expiry always raises :class:`cmd_mox.errors.StdinTimeoutError`. Partial input
is discarded rather than returned, because a truncated body would be presented
to the command as if the caller had really sent it. Text read through a
descriptor or a Windows handle is decoded with the stream's own encoding and
error handler, and universal-newline translation follows the host, matching
``TextIOWrapper.read``.

The worker registry in :func:`outstanding_workers` exists for the script entry
point. A fallback worker cannot be interrupted, so the only safe way to let the
process exit without leaving it behind is :func:`os._exit` once the diagnostic
has been flushed.

Examples
--------
``stdin_text = read_stdin(sys.stdin, timeout)``
"""

from __future__ import annotations

import codecs
import dataclasses
import importlib
import io
import math
import os
import select
import stat
import threading
import time
import typing as typ
from functools import partial

from cmd_mox import _bounded_thread
from cmd_mox import _path_utils as path_utils
from cmd_mox.errors import StdinTimeoutError

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Largest single wait ``poll`` accepts, in milliseconds, as ``int`` ticks.
MAX_POLL_TIMEOUT_MS: typ.Final[int] = 2_147_483_647
#: Chunk size for raw descriptor and Windows handle reads.
_READ_CHUNK: typ.Final[int] = 64 * 1024
#: Chunk size for buffered and polled reads, matching ``BufferedReader.read1``.
_POLL_CHUNK: typ.Final[int] = 8 * 1024
#: ``ReadFile`` reports a closed pipe through this ``winerror`` value.
_ERROR_BROKEN_PIPE: typ.Final[int] = 109
#: Bounded wait for a cancelled Windows reader to unwind.
_CANCEL_GRACE: typ.Final[float] = 1.0

type _MonotonicClock = cabc.Callable[[], float]
type _Strategy = typ.Literal["win32", "regular", "poll", "fallback"]
type _Abandon = cabc.Callable[[threading.Thread], None]

_WORKER_LOCK = threading.Lock()
_WORKERS: list[threading.Thread] = []


class _Poller(typ.Protocol):
    """Minimal polling interface required by the bounded stdin reader."""

    def poll(self, timeout: int, /) -> list[tuple[int, int]]:
        """Wait up to *timeout* milliseconds for descriptor events."""


class _StdinStream(typ.Protocol):
    """Minimal text stream interface used by the capture strategies.

    ``fileno`` is deliberately absent: in-memory streams do not have one, and
    the strategies that need a descriptor resolve it dynamically.
    """

    def isatty(self) -> bool:
        """Return whether the stream is attached to a terminal."""

    @property
    def encoding(self) -> str | None:
        """Text encoding used by the stream."""

    @property
    def errors(self) -> str | None:
        """Error handler used by the stream's decoder."""

    def read(self) -> str:
        """Read text directly when the stream cannot be bounded."""


class _DescribedStream(typ.Protocol):
    """Stream that can expose the descriptor behind its text interface."""

    def fileno(self) -> int:
        """Return the stream's file descriptor."""


class _Msvcrt(typ.Protocol):
    """Subset of ``msvcrt`` needed to resolve a CRT descriptor to a handle."""

    def get_osfhandle(self, descriptor: int) -> int:
        """Return the Windows handle behind *descriptor*."""


class _SelectPoller:
    """Adapt ``select.select`` to the polling interface used by the shim."""

    def __init__(self, descriptor: int) -> None:
        """Bind the descriptor watched by this polling adapter."""
        self.descriptor = descriptor

    def poll(self, timeout: int, /) -> list[tuple[int, int]]:
        """Wait up to *timeout* milliseconds for stdin to become readable.

        Returns
        -------
        list[tuple[int, int]]
            A single readiness event when stdin is ready, or an empty list on
            timeout.
        """
        try:
            readable, _, _ = select.select([self.descriptor], [], [], timeout / 1_000)
        except ValueError:
            # select() rejects negative timeouts; the caller has already
            # established that the deadline is in the future.
            return []
        return [(self.descriptor, 1)] if readable else []


@dataclasses.dataclass(frozen=True, slots=True)
class _Plan:
    """Strategy chosen for one stream, plus what that strategy needs."""

    strategy: _Strategy
    descriptor: int | None = None
    poller: _Poller | None = None
    handle: object | None = None


@dataclasses.dataclass(frozen=True, slots=True)
class _Capture:
    """Everything one bounded capture needs, chosen once up front."""

    stream: _StdinStream
    timeout: float
    deadline: float
    clock: _MonotonicClock
    plan: _Plan


@dataclasses.dataclass(frozen=True, slots=True)
class _PollContext:
    """Descriptor, poller and shared deadline for one polled read."""

    descriptor: int
    poller: _Poller
    timeout: float
    deadline: float
    clock: _MonotonicClock


@dataclasses.dataclass(frozen=True, slots=True)
class _WorkerSpec:
    """Deadline and abandonment policy for one daemon reader worker."""

    name: str
    timeout: float
    deadline: float
    clock: _MonotonicClock
    abandon: _Abandon


def outstanding_workers() -> tuple[threading.Thread, ...]:
    """Return the reader workers still blocked after their deadline.

    Finished workers are pruned, so the result only ever names threads that
    would still be running if the interpreter shut down.

    Returns
    -------
    tuple[threading.Thread, ...]
        Live daemon workers abandoned by earlier captures.
    """
    with _WORKER_LOCK:
        _WORKERS[:] = [worker for worker in _WORKERS if worker.is_alive()]
        return tuple(_WORKERS)


def _register_worker(worker: threading.Thread) -> None:
    """Record *worker* while it is still alive, pruning finished entries."""
    with _WORKER_LOCK:
        _WORKERS[:] = [
            existing
            for existing in _WORKERS
            if existing is not worker and existing.is_alive()
        ]
        if worker.is_alive():
            _WORKERS.append(worker)


def read_stdin(
    stream: _StdinStream,
    timeout: float,
    *,
    clock: _MonotonicClock = time.monotonic,
) -> str:
    """Read *stream* through EOF, giving up after *timeout* seconds.

    Parameters
    ----------
    stream:
        Text stream to capture. A stream that reports itself as a terminal is
        not read at all.
    timeout:
        Deadline in seconds, measured from the start of the capture. The shim
        passes the validated ``CMOX_IPC_TIMEOUT`` value.
    clock:
        Monotonic clock used to measure the deadline.

    Returns
    -------
    str
        The decoded text, empty for a terminal or empty input.

    Notes
    -----
    :class:`cmd_mox.errors.StdinTimeoutError` means the deadline expired before
    EOF and the partial input was discarded. I/O and decoding failures
    propagate from whichever read strategy ran; the shim reports them through
    its own bounded diagnostic.
    """
    if stream.isatty():
        # An interactive prompt belongs to the user; the shim must not consume it.
        return ""
    # The deadline is computed once and shared by every step below, so a slow
    # stream cannot accumulate a fresh budget per chunk.
    deadline = clock() + timeout
    if isinstance(stream, io.StringIO):
        # In-memory streams cannot block, so no worker or deadline is needed.
        return stream.read()
    capture = _Capture(stream, timeout, deadline, clock, _plan_stdin(stream))
    match capture.plan.strategy:
        case "win32":
            return _read_win32(capture)
        case "regular":
            return _read_regular_file(capture)
        case "poll":
            return _read_polled(capture)
        case _:
            return _read_fallback(capture)


def _plan_stdin(stream: _StdinStream) -> _Plan:
    """Choose the bounded read strategy for *stream*.

    Returns
    -------
    _Plan
        The selected strategy with the descriptor, poller or handle it needs.
    """
    if path_utils.IS_WINDOWS:
        handle = _stdin_handle(stream)
        return _Plan("win32", handle=handle) if handle else _Plan("fallback")
    try:
        descriptor = _stream_descriptor(stream)
    except (AttributeError, OSError, ValueError):
        # In-memory and test-double streams have no descriptor to wait on.
        return _Plan("fallback")
    poller = _create_stdin_poller(descriptor)
    if poller is None:
        return _Plan("fallback")
    if _is_regular_file(descriptor):
        return _Plan("regular", descriptor=descriptor)
    return _Plan("poll", descriptor=descriptor, poller=poller)


def _create_stdin_poller(descriptor: int) -> _Poller | None:
    """Choose a waitable interface, or signal the direct-read fallback.

    Returns
    -------
    _Poller or None
        A polling adapter that accepted a zero-timeout probe, or ``None`` when
        neither mechanism can wait on *descriptor*.
    """
    try:
        poller = select.poll()
        poller.register(descriptor, select.POLLIN | select.POLLHUP)
        probe = poller.poll(0)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    else:
        if not any(mask & select.POLLNVAL for _descriptor, mask in probe):
            return poller

    try:
        select_poller = _SelectPoller(descriptor)
        select_poller.poll(0)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return select_poller


def _is_regular_file(descriptor: int) -> bool:
    """Report whether *descriptor* is backed by a regular file.

    Returns
    -------
    bool
        ``True`` for a regular file, and also for a descriptor whose status
        cannot be queried; such a descriptor is then read on a worker.
    """
    try:
        return stat.S_ISREG(os.fstat(descriptor).st_mode)
    except OSError:
        return False


def _stdin_handle(stream: _StdinStream) -> object | None:
    """Resolve the Windows handle behind *stream*.

    Returns
    -------
    object or None
        The ``HANDLE`` for *stream*, or ``None`` when Windows cannot provide
        one, such as for an in-memory test double.
    """
    try:
        msvcrt = typ.cast("_Msvcrt", importlib.import_module("msvcrt"))
        return msvcrt.get_osfhandle(_stream_descriptor(stream))
    except (AttributeError, ImportError, OSError, ValueError):
        return None


def _stream_descriptor(stream: _StdinStream) -> int:
    """Return the descriptor behind *stream*.

    Returns
    -------
    int
        The stream's file descriptor.

    Notes
    -----
    A stream that, like an in-memory test double, has no ``fileno`` raises
    ``AttributeError`` from the cast dereference; callers treat that as a
    missing descriptor.
    """
    return typ.cast("_DescribedStream", stream).fileno()


def _windows_modules() -> tuple[object, object, object] | None:
    """Return ``(win32file, win32api, pywintypes)`` when pywin32 is present.

    Returns
    -------
    tuple[object, object, object] or None
        The modules used to read and cancel a handle read, or ``None`` when
        they are unavailable.
    """
    if not path_utils.IS_WINDOWS:
        return None
    try:
        return (
            importlib.import_module("win32file"),
            importlib.import_module("win32api"),
            importlib.import_module("pywintypes"),
        )
    except ImportError:
        return None


def _stdin_decoder(stream: _StdinStream) -> io.IncrementalNewlineDecoder:
    """Build a decoder using the supplied stream's text settings.

    Returns
    -------
    io.IncrementalNewlineDecoder
        Decoder configured for the stream's encoding and the host's newline
        translation, matching ``TextIOWrapper.read``.
    """
    encoding = getattr(stream, "encoding", None) or "utf-8"
    errors = getattr(stream, "errors", None) or "strict"
    return io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder(encoding)(errors=errors),
        translate=path_utils.IS_WINDOWS,
    )


def _decode(decoder: io.IncrementalNewlineDecoder, chunk: bytes) -> str:
    """Decode one chunk, finalising the decoder at EOF.

    Returns
    -------
    str
        Decoded text for *chunk*.
    """
    return decoder.decode(chunk, final=not chunk)


def _worker_value[T](outcome: dict[str, object]) -> T:
    """Return the worker's value, re-raising any error it captured.

    Returns
    -------
    T
        The value returned by the abandoned worker.

    Raises
    ------
    BaseException
        Whatever the worker captured, re-raised on the caller's thread.
    """  # ruff: ignore[docstring-extraneous-exception] - the stored worker-thread exception is re-raised verbatim and stays caller-visible
    error = outcome.get("error")
    if isinstance(error, BaseException):
        raise error
    return typ.cast("T", outcome.get("value"))


def _await_worker[T](func: cabc.Callable[[], T], spec: _WorkerSpec) -> T:
    """Run *func* on a daemon worker until it finishes or *spec* expires.

    Returns
    -------
    T
        The value returned by *func*.

    Raises
    ------
    StdinTimeoutError
        If the deadline expires first. The worker is handed to
        :func:`outstanding_workers` when it is still alive.
    """
    outcome: dict[str, object] = {}
    finished = threading.Event()

    def run() -> None:
        try:
            _bounded_thread.stash_outcome(outcome, func)()
        finally:
            finished.set()

    worker = threading.Thread(target=run, name=spec.name, daemon=True)
    worker.start()
    remaining = spec.deadline - spec.clock()
    if remaining > 0 and finished.wait(remaining):
        return _worker_value(outcome)
    spec.abandon(worker)
    _register_worker(worker)
    raise StdinTimeoutError(spec.timeout, worker_alive=worker.is_alive())


def _do_not_cancel(_worker: threading.Thread) -> None:
    """Leave *worker* blocked; the fallback read cannot be interrupted."""


def _read_fallback(capture: _Capture) -> str:
    """Read *capture* on a tracked daemon worker.

    Returns
    -------
    str
        The stream's text, read through EOF.
    """
    return _await_worker(
        capture.stream.read,
        _WorkerSpec(
            name="cmd-mox-stdin-reader",
            timeout=capture.timeout,
            deadline=capture.deadline,
            clock=capture.clock,
            abandon=_do_not_cancel,
        ),
    )


def _read_regular_file(capture: _Capture) -> str:
    """Read regular-file stdin on a worker, bounded by the shared deadline.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    descriptor = capture.plan.descriptor
    if descriptor is None:
        return _read_fallback(capture)
    decoder = _stdin_decoder(capture.stream)

    def read_all() -> str:
        chunks: list[str] = []
        # Raw reads avoid holding TextIOWrapper's buffer lock if this worker
        # outlives the deadline and the interpreter starts shutting down.
        while chunk := os.read(descriptor, _READ_CHUNK):
            chunks.append(decoder.decode(chunk))
        chunks.append(_decode(decoder, b""))
        return "".join(chunks)

    return _await_worker(
        read_all,
        _WorkerSpec(
            name="cmd-mox-stdin-reader",
            timeout=capture.timeout,
            deadline=capture.deadline,
            clock=capture.clock,
            abandon=_do_not_cancel,
        ),
    )


def _read_win32(capture: _Capture) -> str:
    """Read a Windows stdin handle, cancelling the read on expiry.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    handle = capture.plan.handle
    modules = _windows_modules()
    if handle is None or modules is None:
        return _read_fallback(capture)
    win32_file = typ.cast("_Win32File", modules[0])
    win32_api = typ.cast("_bounded_thread._Win32Api", modules[1])
    win32_errors = typ.cast("_bounded_thread._PyWinTypes", modules[2])
    request_cancel = partial(
        _bounded_thread.request_synchronous_io_cancel,
        win32file=win32_file,
        win32api=win32_api,
        pywintypes=win32_errors,
    )
    return _await_worker(
        partial(
            _read_win32_until_eof,
            win32_file,
            win32_errors,
            handle,
            _stdin_decoder(capture.stream),
        ),
        _WorkerSpec(
            name="cmd-mox-stdin-reader",
            timeout=capture.timeout,
            deadline=capture.deadline,
            clock=capture.clock,
            abandon=partial(_abandon_win32_reader, request_cancel=request_cancel),
        ),
    )


class _Win32File(_bounded_thread._Win32File, typ.Protocol):
    """``win32file`` surface used by both the reader and the canceller."""

    def ReadFile(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self, handle: object, size: int
    ) -> tuple[int, bytes]:
        """Read up to *size* bytes from *handle*."""


def _read_win32_until_eof(
    win32file: _Win32File,
    pywintypes: _bounded_thread._PyWinTypes,
    handle: object,
    decoder: io.IncrementalNewlineDecoder,
) -> str:
    """Read a Windows stdin *handle* through EOF.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    chunks: list[str] = []
    while chunk := _read_win32_chunk(win32file, pywintypes, handle):
        chunks.append(decoder.decode(chunk))
    chunks.append(_decode(decoder, b""))
    return "".join(chunks)


def _read_win32_chunk(
    win32file: _Win32File,
    pywintypes: _bounded_thread._PyWinTypes,
    handle: object,
) -> bytes:
    """Read one chunk from a Windows stdin *handle*.

    Returns
    -------
    bytes
        The bytes read, or ``b""`` once the pipe reports EOF.
    """
    try:
        _status, data = win32file.ReadFile(handle, _READ_CHUNK)
    except pywintypes.error as exc:
        if getattr(exc, "winerror", None) == _ERROR_BROKEN_PIPE:
            return b""
        raise
    return data or b""


def _abandon_win32_reader(
    worker: threading.Thread,
    *,
    request_cancel: cabc.Callable[[threading.Thread], None],
) -> None:
    """Cancel a blocked ``ReadFile`` and wait, bounded, for *worker*."""
    _bounded_thread.cancel_and_await_worker(
        worker,
        _do_not_cancel_for_stdin,
        grace=_CANCEL_GRACE,
        request_cancel=request_cancel,
    )


def _do_not_cancel_for_stdin() -> None:
    """Leave stdin open; only the in-flight read is cancelled."""


def _wait_for_readiness(context: _PollContext) -> None:
    """Block until stdin is readable, or raise once the deadline passes.

    Raises
    ------
    StdinTimeoutError
        If the deadline expires before stdin becomes readable.
    """
    while True:
        remaining = context.deadline - context.clock()
        if remaining <= 0:
            raise StdinTimeoutError(context.timeout, worker_alive=False)
        poll_timeout = min(remaining, MAX_POLL_TIMEOUT_MS / 1_000)
        events = context.poller.poll(max(1, math.ceil(poll_timeout * 1_000)))
        if context.clock() >= context.deadline:
            raise StdinTimeoutError(context.timeout, worker_alive=False)
        if events:
            return


def _poll_context(capture: _Capture) -> _PollContext | None:
    """Build the polled-read context, or ``None`` without a usable descriptor.

    Returns
    -------
    _PollContext or None
        Context for the polled reader, or ``None`` when the plan lacks one.
    """
    descriptor = capture.plan.descriptor
    poller = capture.plan.poller
    if descriptor is None or poller is None:
        return None
    return _PollContext(
        descriptor, poller, capture.timeout, capture.deadline, capture.clock
    )


def _read_polled(capture: _Capture) -> str:
    """Read polled stdin through EOF before the shared deadline.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    context = _poll_context(capture)
    if context is None:
        return _read_fallback(capture)
    read_chunk = getattr(getattr(capture.stream, "buffer", None), "read1", None)
    if not callable(read_chunk):
        return _read_raw_polled(capture, context)
    return _read_buffered(capture, context, read_chunk)


def _read_buffered(
    capture: _Capture,
    context: _PollContext,
    read_chunk: cabc.Callable[[int], bytes | None],
) -> str:
    """Consume buffered stdin without losing text-layer read-ahead.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    decoder = _stdin_decoder(capture.stream)
    chunks: list[str] = []
    while True:
        _wait_for_readiness(context)
        chunk = _read_buffered_chunk(context, read_chunk)
        if chunk is None:
            continue
        if not chunk:
            chunks.append(_decode(decoder, b""))
            return "".join(chunks)
        chunks.append(decoder.decode(chunk))


def _read_buffered_chunk(
    context: _PollContext, read_chunk: cabc.Callable[[int], bytes | None]
) -> bytes | None:
    """Read a chunk while distinguishing a temporary empty read from EOF.

    Returns
    -------
    bytes or None
        The next chunk, empty bytes at EOF, or ``None`` after a readiness wait.
    """
    try:
        chunk = read_chunk(_POLL_CHUNK)
    except BlockingIOError:
        # read1() may report "some bytes are ready, not enough for a line".
        chunk = None
    if chunk:
        return chunk
    if chunk == b"":
        return b""
    _wait_for_readiness(context)
    return None


def _read_raw_polled(capture: _Capture, context: _PollContext) -> str:
    """Read custom stdin descriptors that have no buffered reader.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    decoder = _stdin_decoder(capture.stream)
    chunks: list[str] = []
    while True:
        _wait_for_readiness(context)
        chunk = os.read(context.descriptor, _POLL_CHUNK)
        chunks.append(_decode(decoder, chunk))
        if not chunk:
            return "".join(chunks)
