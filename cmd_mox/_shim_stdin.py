"""Read the command shim's stdin, bounding waits where supported.

On POSIX, pollable streams wait with ``poll()`` and fall back to ``select()``;
regular files use a daemon worker because readiness polling cannot bound a file
read. Windows streams, streams without a file descriptor, and descriptors
unsupported by both pollers retain direct reads. ``on_error`` handles timeout,
I/O, and decode failures, and must not return because the shim cannot safely
continue without complete stdin.

Examples
--------
``stdin_text = _read_stdin_until_eof(
    timeout, deadline=None, on_error=fail_ipc, dependencies=dependencies
)``
"""

from __future__ import annotations

import codecs
import collections.abc as cabc
import dataclasses
import io
import math
import os
import queue
import select
import stat
import sys
import threading
import typing as typ

from cmd_mox import _path_utils as path_utils

MAX_POLL_TIMEOUT_MS: typ.Final[int] = 2_147_483_647
type _ErrorHandler = cabc.Callable[[Exception], typ.NoReturn]


class _Poller(typ.Protocol):
    """Minimal polling interface required by the bounded stdin reader."""

    def poll(self, timeout: int, /) -> list[tuple[int, int]]:
        """Wait up to timeout milliseconds for file-descriptor events."""


class _StdinStream(typ.Protocol):
    """Minimal text stream interface used by the stdin source."""

    @property
    def encoding(self) -> str | None:
        """Text encoding used by the stream."""

    @property
    def errors(self) -> str | None:
        """Error handler used by the stream's decoder."""

    def fileno(self) -> int:
        """Return the descriptor used by the stream."""

    def read(self) -> str:
        """Read text directly when descriptor polling is unavailable."""


type _MonotonicClock = cabc.Callable[[], float]


@dataclasses.dataclass(frozen=True, slots=True)
class _StdinSource:
    """Bundle process stdin operations behind one narrow testable boundary."""

    stream: _StdinStream
    create_poller: cabc.Callable[[int], _Poller | None]
    read_descriptor: cabc.Callable[[int, int], bytes]
    stat_descriptor: cabc.Callable[[int], os.stat_result]


@dataclasses.dataclass(frozen=True, slots=True)
class _StdinReadDependencies:
    """Bind stdin operations and the clock used by one bounded read."""

    source: _StdinSource
    clock: _MonotonicClock


def _system_stdin_source() -> _StdinSource:
    """Bind the live stdin stream and operating-system adapters.

    Returns
    -------
    _StdinSource
        Source using process stdin and the operating-system adapters.
    """
    return _StdinSource(
        stream=sys.stdin,
        create_poller=_create_stdin_poller,
        read_descriptor=os.read,
        stat_descriptor=os.fstat,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _ReadContext:
    """Shared descriptor, deadline, and failure policy for one stdin read."""

    descriptor: int
    poller: _Poller
    deadline: float
    on_error: _ErrorHandler
    dependencies: _StdinReadDependencies


class _SelectPoller:
    """Adapt select.select to the polling interface used by the shim."""

    def __init__(self, descriptor: int) -> None:
        """Bind the descriptor watched by this polling adapter."""
        self.descriptor = descriptor

    def poll(self, timeout: int, /) -> list[tuple[int, int]]:
        """Wait up to timeout milliseconds for stdin to become readable.

        Returns
        -------
        list[tuple[int, int]]
            A non-empty event list when stdin is ready, or an empty list on
            timeout.
        """
        readable, _, _ = select.select([self.descriptor], [], [], timeout / 1_000)
        return [(self.descriptor, 1)] if readable else []


def _create_stdin_poller(descriptor: int) -> _Poller | None:
    """Choose a waitable interface or signal direct-read fallback.

    Returns
    -------
    _Poller or None
        A polling adapter, or None when neither mechanism can wait.
    """
    try:
        poller = select.poll()
        poller.register(descriptor, select.POLLIN | select.POLLHUP)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    else:
        return poller

    try:
        poller = _SelectPoller(descriptor)
        poller.poll(0)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return poller


def _stdin_decoder(stream: _StdinStream) -> io.IncrementalNewlineDecoder:
    """Build a decoder using the supplied stream's text settings.

    Returns
    -------
    io.IncrementalNewlineDecoder
        Decoder configured for the current stdin encoding and newline rules.
    """
    encoding = getattr(stream, "encoding", None) or "utf-8"
    errors = getattr(stream, "errors", None) or "strict"
    return io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder(encoding)(errors=errors),
        # POSIX sys.stdin retains carriage returns rather than translating them.
        translate=False,
    )


def _poll_stdin(context: _ReadContext) -> list[tuple[int, int]] | None:
    """Wait once for stdin, returning None only after the deadline.

    Returns
    -------
    list[tuple[int, int]] or None
        Readiness events, an empty list on an intermediate timeout, or None
        after the deadline.
    """
    remaining = context.deadline - context.dependencies.clock()
    if remaining <= 0:
        return None

    poll_timeout = min(remaining, MAX_POLL_TIMEOUT_MS / 1_000)
    try:
        events = context.poller.poll(max(1, math.ceil(poll_timeout * 1_000)))
    except OSError as exc:
        context.on_error(exc)
    if context.dependencies.clock() < context.deadline:
        return events
    return None


def _decode_stdin_chunk(
    decoder: io.IncrementalNewlineDecoder,
    chunk: bytes,
    on_error: _ErrorHandler,
) -> str:
    """Decode one stdin chunk, reporting text errors through the shim.

    Returns
    -------
    str
        Decoded text with universal newlines translated.
    """
    try:
        return decoder.decode(chunk, final=not chunk)
    except UnicodeError as exc:
        return on_error(exc)


def _read_stdin_until_eof(
    timeout: float,
    *,
    deadline: float | None,
    on_error: _ErrorHandler,
    dependencies: _StdinReadDependencies,
) -> str:
    """Read non-interactive stdin, applying the deadline where supported.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    stdin_source = dependencies.source
    monotonic = dependencies.clock

    if path_utils.IS_WINDOWS:
        return _read_direct_stdin(stdin_source.stream, on_error)

    try:
        descriptor = stdin_source.stream.fileno()
    except (AttributeError, OSError, ValueError):
        # Test doubles and non-file streams retain their established direct read.
        return _read_direct_stdin(stdin_source.stream, on_error)
    poller = stdin_source.create_poller(descriptor)
    if poller is None:
        # Preserve direct reads for descriptors unsupported by both waiters.
        return _read_direct_stdin(stdin_source.stream, on_error)

    read_deadline = deadline if deadline is not None else monotonic() + timeout
    try:
        is_regular_file = stat.S_ISREG(stdin_source.stat_descriptor(descriptor).st_mode)
    except OSError:
        is_regular_file = False
    if is_regular_file:
        return _read_regular_stdin_until_deadline(
            descriptor,
            read_deadline,
            on_error,
            dependencies=dependencies,
        )
    return _read_polled_stdin(
        _ReadContext(descriptor, poller, read_deadline, on_error, dependencies)
    )


def _read_direct_stdin(stream: _StdinStream, on_error: _ErrorHandler) -> str:
    """Route failures from an unpolled text read through the shim boundary.

    Returns
    -------
    str
        Captured stdin text or the value returned by the error handler.
    """
    try:
        return stream.read()
    except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
        return on_error(exc)


def _read_regular_stdin_until_deadline(
    descriptor: int,
    deadline: float,
    on_error: _ErrorHandler,
    *,
    dependencies: _StdinReadDependencies,
) -> str:
    """Read regular-file stdin in a daemon worker bounded by deadline.

    Returns
    -------
    str
        The text read from stdin through EOF.
    """
    result_queue: queue.Queue[tuple[str, Exception | None]] = queue.Queue()
    decoder = _stdin_decoder(dependencies.source.stream)

    def read_stdin() -> None:
        """Queue decoded descriptor input or its read error for the caller."""
        try:
            chunks: list[str] = []
            # Raw reads avoid holding TextIOWrapper's buffer lock if this worker
            # outlives the deadline and the main thread exits the interpreter.
            while chunk := dependencies.source.read_descriptor(descriptor, 64 * 1024):
                chunks.append(decoder.decode(chunk))
            chunks.append(decoder.decode(b"", final=True))
            result_queue.put(("".join(chunks), None))
        except (OSError, UnicodeError, ValueError, RuntimeError) as exc:
            result_queue.put(("", exc))

    try:
        threading.Thread(
            target=read_stdin,
            name="cmd-mox-stdin-reader",
            daemon=True,
        ).start()
    except (OSError, RuntimeError) as exc:
        on_error(exc)

    stdin_data, error = _await_regular_stdin_read(
        result_queue, deadline, on_error, clock=dependencies.clock
    )
    if error is not None:
        on_error(error)
    return stdin_data


def _await_regular_stdin_read(
    result_queue: queue.Queue[tuple[str, Exception | None]],
    deadline: float,
    on_error: _ErrorHandler,
    *,
    clock: _MonotonicClock,
) -> tuple[str, Exception | None]:
    """Wait for the regular-file reader without exceeding the deadline.

    Returns
    -------
    tuple[str, Exception or None]
        The read text and any error raised by the reader.
    """
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            on_error(TimeoutError("timed out reading stdin"))
        try:
            result = result_queue.get(timeout=min(remaining, threading.TIMEOUT_MAX))
        except queue.Empty:
            continue
        if clock() >= deadline:
            on_error(TimeoutError("timed out reading stdin"))
        return result


def _read_polled_stdin(context: _ReadContext) -> str:
    """Read polled stdin through EOF before its monotonic deadline.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    stream = context.dependencies.source.stream
    buffered_read = getattr(getattr(stream, "buffer", None), "read1", None)
    if not callable(buffered_read):
        return _read_raw_polled_stdin(context)

    return _read_buffered_stdin(context, buffered_read)


def _read_buffered_stdin(
    context: _ReadContext,
    read_chunk: cabc.Callable[[int], bytes | None],
) -> str:
    """Consume buffered stdin without losing text-layer read-ahead.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    decoder = _stdin_decoder(context.dependencies.source.stream)
    chunks: list[str] = []
    while True:
        if context.dependencies.clock() >= context.deadline:
            context.on_error(TimeoutError("timed out reading stdin"))
        events = _poll_stdin(context)
        if events is None or context.dependencies.clock() >= context.deadline:
            context.on_error(TimeoutError("timed out reading stdin"))
        if not events:
            continue
        chunk = _read_buffered_chunk(context, read_chunk)
        if chunk is None:
            continue
        if not chunk:
            chunks.append(_decode_stdin_chunk(decoder, b"", context.on_error))
            return "".join(chunks)
        chunks.append(_decode_stdin_chunk(decoder, chunk, context.on_error))


def _read_buffered_chunk(
    context: _ReadContext,
    read_chunk: cabc.Callable[[int], bytes | None],
) -> bytes | None:
    """Read a chunk while distinguishing a temporary empty read from EOF.

    Returns
    -------
    bytes or None
        The next chunk, empty bytes at EOF, or None after a readiness wait.
    """
    try:
        chunk = read_chunk(8_192)
    except BlockingIOError:
        chunk = None
    except OSError as exc:
        context.on_error(exc)

    if chunk:
        return chunk
    if chunk == b"":
        return b""
    _wait_for_stdin(context)
    return None


def _wait_for_stdin(context: _ReadContext) -> None:
    """Wait for stdin readiness or report when its deadline expires."""
    if _poll_stdin(context) is None:
        context.on_error(TimeoutError("timed out reading stdin"))


def _read_raw_polled_stdin(context: _ReadContext) -> str:
    """Read custom stdin descriptors that have no buffered reader.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    source = context.dependencies.source
    decoder = _stdin_decoder(source.stream)
    chunks: list[str] = []
    while True:
        events = _poll_stdin(context)
        if events is None:
            context.on_error(TimeoutError("timed out reading stdin"))
        if not events:
            continue

        try:
            chunk = source.read_descriptor(context.descriptor, 8_192)
        except OSError as exc:
            context.on_error(exc)
        chunks.append(_decode_stdin_chunk(decoder, chunk, context.on_error))
        if not chunk:
            return "".join(chunks)
