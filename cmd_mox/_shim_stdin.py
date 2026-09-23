"""Bounded stdin reading for the command shim."""

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
import time
import typing as typ

from cmd_mox import _path_utils as path_utils

MAX_POLL_TIMEOUT_MS: typ.Final[int] = 2_147_483_647
type _ErrorHandler = cabc.Callable[[Exception], typ.NoReturn]


class _Poller(typ.Protocol):
    """Minimal polling interface required by the bounded stdin reader."""

    def poll(self, timeout: int, /) -> list[tuple[int, int]]:
        """Wait up to timeout milliseconds for file-descriptor events."""


@dataclasses.dataclass(frozen=True, slots=True)
class _ReadContext:
    """Shared descriptor, deadline, and failure policy for one stdin read."""

    descriptor: int
    poller: _Poller
    deadline: float
    on_error: _ErrorHandler


class _SelectPoller:
    """Adapt select.select to the polling interface used by the shim."""

    def __init__(self, descriptor: int) -> None:
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


def _stdin_decoder() -> io.IncrementalNewlineDecoder:
    """Build a TextIO-compatible decoder for piped standard input.

    Returns
    -------
    io.IncrementalNewlineDecoder
        Decoder configured for the current stdin encoding and newline rules.
    """
    encoding = getattr(sys.stdin, "encoding", None) or "utf-8"
    errors = getattr(sys.stdin, "errors", None) or "strict"
    return io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder(encoding)(errors=errors),
        translate=True,
    )


def _poll_stdin(poller: _Poller, deadline: float) -> list[tuple[int, int]] | None:
    """Wait once for stdin, returning None only after the deadline.

    Returns
    -------
    list[tuple[int, int]] or None
        Readiness events, an empty list on an intermediate timeout, or None
        after the deadline.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None

    poll_timeout = min(remaining, MAX_POLL_TIMEOUT_MS / 1_000)
    events = poller.poll(max(1, math.ceil(poll_timeout * 1_000)))
    if events or time.monotonic() < deadline:
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
) -> str:
    """Read non-interactive stdin before its shared monotonic deadline.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    if path_utils.IS_WINDOWS:
        return sys.stdin.read()

    try:
        descriptor = sys.stdin.fileno()
    except (AttributeError, OSError, ValueError):
        # Test doubles and non-file streams retain their established direct read.
        return sys.stdin.read()
    poller = _create_stdin_poller(descriptor)
    if poller is None:
        # Preserve direct reads for descriptors unsupported by both waiters.
        return sys.stdin.read()

    read_deadline = deadline if deadline is not None else time.monotonic() + timeout
    try:
        is_regular_file = stat.S_ISREG(os.fstat(descriptor).st_mode)
    except OSError:
        is_regular_file = False
    if is_regular_file:
        return _read_regular_stdin_until_deadline(read_deadline, on_error)
    return _read_polled_stdin(_ReadContext(descriptor, poller, read_deadline, on_error))


def _read_regular_stdin_until_deadline(deadline: float, on_error: _ErrorHandler) -> str:
    """Read regular-file stdin in a daemon worker bounded by deadline.

    Returns
    -------
    str
        The text read from stdin through EOF.
    """
    result_queue: queue.Queue[tuple[str, Exception | None]] = queue.Queue()
    stdin = sys.stdin

    def read_stdin() -> None:
        try:
            result_queue.put((stdin.read(), None))
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

    stdin_data, error = _await_regular_stdin_read(result_queue, deadline, on_error)
    if error is not None:
        on_error(error)
    return stdin_data


def _await_regular_stdin_read(
    result_queue: queue.Queue[tuple[str, Exception | None]],
    deadline: float,
    on_error: _ErrorHandler,
) -> tuple[str, Exception | None]:
    """Wait for the regular-file reader without exceeding the deadline.

    Returns
    -------
    tuple[str, Exception or None]
        The read text and any error raised by the reader.
    """
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            on_error(TimeoutError("timed out reading stdin"))
        try:
            result = result_queue.get(timeout=min(remaining, threading.TIMEOUT_MAX))
        except queue.Empty:
            continue
        if time.monotonic() >= deadline:
            on_error(TimeoutError("timed out reading stdin"))
        return result


def _read_polled_stdin(context: _ReadContext) -> str:
    """Read polled stdin through EOF before its monotonic deadline.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    buffered_read = getattr(getattr(sys.stdin, "buffer", None), "read1", None)
    if not callable(buffered_read):
        return _read_raw_polled_stdin(context)

    try:
        was_blocking = os.get_blocking(context.descriptor)
        os.set_blocking(context.descriptor, False)
    except OSError as exc:
        context.on_error(exc)
    try:
        return _read_buffered_stdin(context, buffered_read)
    finally:
        try:
            os.set_blocking(context.descriptor, was_blocking)
        except OSError as exc:
            context.on_error(exc)


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
    decoder = _stdin_decoder()
    chunks: list[str] = []
    while True:
        if time.monotonic() >= context.deadline:
            context.on_error(TimeoutError("timed out reading stdin"))
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
        return _read_raw_chunk_after_empty_buffer(context)
    _wait_for_stdin(context)
    return None


def _read_raw_chunk_after_empty_buffer(context: _ReadContext) -> bytes | None:
    """Probe the descriptor after its buffered reader reports no data.

    Returns
    -------
    bytes or None
        Raw input, empty bytes at EOF, or None after a readiness wait.
    """
    try:
        return os.read(context.descriptor, 8_192)
    except BlockingIOError:
        _wait_for_stdin(context)
        return None
    except OSError as exc:
        return context.on_error(exc)


def _wait_for_stdin(context: _ReadContext) -> None:
    """Wait for stdin readiness or report when its deadline expires."""
    if _poll_stdin(context.poller, context.deadline) is None:
        context.on_error(TimeoutError("timed out reading stdin"))


def _read_raw_polled_stdin(context: _ReadContext) -> str:
    """Read custom stdin descriptors that have no buffered reader.

    Returns
    -------
    str
        The decoded text read through EOF.
    """
    decoder = _stdin_decoder()
    chunks: list[str] = []
    while True:
        events = _poll_stdin(context.poller, context.deadline)
        if events is None:
            context.on_error(TimeoutError("timed out reading stdin"))
        if not events:
            continue

        try:
            chunk = os.read(context.descriptor, 8_192)
        except OSError as exc:
            context.on_error(exc)
        chunks.append(_decode_stdin_chunk(decoder, chunk, context.on_error))
        if not chunk:
            return "".join(chunks)
