"""Unit tests for the bounded stdin capture strategies.

Covers the fast paths (terminal and in-memory streams), the tracked
fallback worker, and the POSIX polled and regular-file strategies. The
Windows handle strategy and its plan selection live in
``test_stdin_capture_windows.py``.
"""

from __future__ import annotations

import io
import os
import threading
import types
import typing as typ

import pytest

from cmd_mox import _stdin_capture as capture
from cmd_mox.errors import StdinTimeoutError
from cmd_mox.unittests._stdin_capture_fakes import (
    _BlockingStream,
    _Clock,
    _DescriptorStream,
    _ErrorPoller,
    _install_completion_clock,
    _NoFileno,
    _open_pipe_stream,
    _PollerOSError,
    _read,
    _Stream,
    _StreamError,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

# --- TTY and in-memory fast paths -------------------------------------------


def test_tty_stdin_is_not_read() -> None:
    """A terminal stream is returned as empty without a read call."""
    stream = _Stream("unused", is_tty=True)
    assert _read(stream, 1.0, _Clock()) == "", "a tty should capture no stdin"
    assert stream.read_calls == 0, "a tty must not be read"


def test_string_io_is_read_without_a_worker() -> None:
    """An in-memory stream is read directly, even at a zero deadline."""
    assert _read(io.StringIO("payload"), 0.0, _Clock()) == "payload", (
        "an in-memory stream should be read directly at a zero deadline"
    )


# --- Fallback strategy ------------------------------------------------------


def test_missing_descriptor_reads_through_a_worker() -> None:
    """A stream without ``fileno`` is read on the fallback worker."""
    stream = _NoFileno("fallback data")
    assert _read(stream, 1.0, _Clock()) == "fallback data", (
        "the fallback should return the stream's text"
    )
    assert stream.read_calls == 1, "the fallback should read exactly once"


def test_unpollable_descriptor_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A descriptor both pollers reject is read through the fallback worker."""
    monkeypatch.setattr(capture, "_create_stdin_poller", lambda _descriptor: None)
    stream = _DescriptorStream(0, data="unpollable")
    assert _read(stream, 1.0, _Clock()) == "unpollable", (
        "an unpollable descriptor should still yield its text"
    )
    assert stream.read_calls == 1, "the fallback should read exactly once"


def test_poll_registration_failure_leaves_select_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejected ``poll`` registration still leaves ``select`` as an option."""
    monkeypatch.delattr(capture.select, "poll")
    assert capture._create_stdin_poller(0) is not None, (
        "select should provide a usable fallback poller"
    )


def test_pollnval_probe_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ``POLLNVAL`` probe result marks the descriptor as unpollable."""

    class _InvalidPoller:
        def register(self, _descriptor: int, _mask: int) -> None:
            """Accept registration."""

        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            """Report the descriptor as invalid.

            Returns
            -------
            list[tuple[int, int]]
                One event carrying ``POLLNVAL``.
            """
            return [(0, capture.select.POLLNVAL)]

    def reject(_descriptor: int) -> capture._Poller:
        msg = "closed"
        raise ValueError(msg)

    monkeypatch.setattr(capture.select, "poll", _InvalidPoller)
    monkeypatch.setattr(capture, "_SelectPoller", reject)
    assert capture._create_stdin_poller(0) is None, "POLLNVAL was not detected"


class _PoisonedPoller:
    """Poller that rejects every registration, forcing the ``select`` probe."""

    def register(self, _descriptor: int, _mask: int) -> None:
        """Refuse the descriptor.

        Raises
        ------
        ValueError
            Always, so the first probe fails.
        """
        msg = "descriptor out of range"
        raise ValueError(msg)

    def poll(self, _timeout: int, /) -> list[tuple[int, int]]:
        """Never reached, because registration already failed.

        Returns
        -------
        list[tuple[int, int]]
            No events.
        """
        return []


def test_unselectable_descriptor_plan_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A descriptor ``select`` cannot watch is read directly, not waited on.

    ``select.poll`` has no ``FD_SETSIZE`` limit but ``select.select`` does, so
    a high-numbered descriptor is rejected only when ``_SelectPoller`` is the
    fallback. Swallowing that ``ValueError`` would accept the probe and then
    wait on a poller that can never report readiness, turning a readable
    descriptor into a spurious timeout instead of a direct read.
    """
    monkeypatch.setattr(capture.select, "poll", _PoisonedPoller)

    def reject_select(
        _readables: object, _writables: object, _exceptables: object, _timeout: object
    ) -> tuple[list[int], list[int], list[int]]:
        msg = "filedescriptor out of range in select()"
        raise ValueError(msg)

    monkeypatch.setattr(capture.select, "select", reject_select)
    stream = _DescriptorStream(0, data="unselectable")

    assert capture._plan_stdin(stream).strategy == "fallback", (
        "an unselectable descriptor must plan a direct read"
    )
    assert _read(stream, 0.05, _Clock()) == "unselectable", (
        "the fallback should still capture the stream's text"
    )


class _OverflowingEvent:
    """Event double whose wait mirrors CPython's timeout range check.

    A real :meth:`threading.Event.wait` raises :exc:`OverflowError` once its
    timeout exceeds :data:`threading.TIMEOUT_MAX`. Waiting for such a slice in
    a test would outlive the suite, so this double reproduces the rejection and
    advances a shared clock by each accepted slice instead.
    """

    def __init__(self, clock: _Clock) -> None:
        self.clock = clock
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        """Record *timeout*, rejecting values CPython cannot schedule.

        Returns
        -------
        bool
            ``False``, because the blocked worker never sets the event.

        Raises
        ------
        OverflowError
            If *timeout* exceeds :data:`threading.TIMEOUT_MAX`.
        """
        if timeout is not None and timeout > threading.TIMEOUT_MAX:
            msg = "timestamp out of range for platform time_t"
            raise OverflowError(msg)
        assert timeout is not None, "the capture always waits with a timeout"
        self.waits.append(timeout)
        self.clock.now += timeout
        return False

    def set(self) -> None:
        """Accept the worker's completion signal without changing the wait."""


def test_worker_wait_is_sliced_at_the_platform_timeout_max(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timeout beyond ``threading.TIMEOUT_MAX`` still ends in a diagnostic.

    ``CMOX_IPC_TIMEOUT`` accepts any finite positive float, but
    :meth:`threading.Event.wait` rejects a wait longer than
    :data:`threading.TIMEOUT_MAX`. The worker strategies wait on an event, so
    an unsliced wait would raise :exc:`OverflowError`, which the shim's stdin
    handler does not catch, instead of the bounded timeout diagnostic.
    """
    clock = _Clock()
    stream = _BlockingStream("never returned")
    event = _OverflowingEvent(clock)
    # Only the capture module's view of ``threading`` is replaced; patching the
    # real module would also divert the events ``Thread.__init__`` creates.
    monkeypatch.setattr(
        capture,
        "threading",
        types.SimpleNamespace(Event=lambda: event, Thread=threading.Thread),
    )
    try:
        with pytest.raises(StdinTimeoutError):
            _read(stream, 1e10, clock)
    finally:
        stream.release.set()
        stream.finished.wait(5.0)

    assert event.waits, "the worker was never awaited"
    assert all(wait <= threading.TIMEOUT_MAX for wait in event.waits), (
        "a wait beyond the platform limit reached Event.wait"
    )
    assert len(event.waits) > 1, "a timeout beyond the limit was not sliced"


def test_fallback_timeout_discards_partial_input() -> None:
    """An expired fallback read raises instead of returning partial text."""
    stream = _BlockingStream("late data")
    try:
        with pytest.raises(StdinTimeoutError) as excinfo:
            _read(stream, 0.05, _Clock())
    finally:
        stream.release.set()
        stream.finished.wait(5.0)

    assert excinfo.value.timeout == pytest.approx(0.05), "the timeout was not recorded"
    assert "late data" not in str(excinfo.value), "partial input leaked"
    # The flag is a snapshot taken when the deadline expired: the fallback
    # worker cannot be interrupted, so it is still blocked at that moment.
    assert excinfo.value.worker_alive is True, "the blocked worker should live"
    assert stream.finished.is_set(), "releasing the stream did not end the worker"


def test_fallback_worker_registry_prunes_finished_workers() -> None:
    """A finished worker leaves the registry once it is observed."""
    stream = _BlockingStream("eventual")
    try:
        with pytest.raises(StdinTimeoutError) as excinfo:
            _read(stream, 0.05, _Clock())
        assert excinfo.value.worker_alive is True, "the blocked worker should live"
        assert capture.outstanding_workers(), "the live worker was not registered"
    finally:
        stream.release.set()
        stream.finished.wait(5.0)

    for worker in capture.outstanding_workers():
        worker.join(5.0)
    assert capture.outstanding_workers() == (), (
        "the finished worker was not pruned from the registry"
    )


def test_fallback_rereaises_worker_errors() -> None:
    """A stream error captured by the worker surfaces on the caller's thread."""

    class _FailingStream(_Stream):
        def read(self) -> str:
            """Fail the read as a broken stream would.

            Raises
            ------
            _StreamError
                Always.
            """
            msg = "stream exploded"
            raise _StreamError(msg)

    with pytest.raises(_StreamError, match="stream exploded"):
        _read(_FailingStream(), 1.0, _Clock())


def test_fallback_completes_before_the_deadline() -> None:
    """Data available before the deadline is returned unchanged."""
    assert _read(_Stream("prompt data"), 1.0, _Clock()) == "prompt data", (
        "data available before the deadline must be returned unchanged"
    )


# --- Polled strategy --------------------------------------------------------


def test_expiry_exactly_at_the_deadline_raises() -> None:
    """A deadline that has just been reached is an expiry, not a wait."""
    with pytest.raises(StdinTimeoutError):
        _read(_DescriptorStream(0), 0.0, _Clock())


def test_polled_read_returns_data_before_the_deadline() -> None:
    """A pollable descriptor yields the text queued before the deadline."""
    with _open_pipe_stream(b"polled payload") as stream:
        assert _read(stream, 5.0, _Clock()) == "polled payload", (
            "a pollable descriptor should yield the text queued for it"
        )


def test_polled_read_does_not_advance_the_injected_clock() -> None:
    """The polled strategy consults the injected clock, not wall time."""
    clock = _Clock()
    with _open_pipe_stream(b"injected") as stream:
        assert _read(stream, 1.0, clock) == "injected", (
            "the polled read should return the piped text"
        )
    assert clock.now == pytest.approx(0.0), "a fast read must not advance the clock"


def test_polled_read_preserves_newlines_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Piped carriage returns survive on POSIX, matching ``TextIOWrapper``."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", False)
    with _open_pipe_stream(b"first\r\nsecond\rthird") as stream:
        assert _read(stream, 5.0, _Clock()) == "first\r\nsecond\rthird", (
            "POSIX capture must not translate carriage returns"
        )


def test_decoder_matches_text_wrapper_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows decoding translates newlines exactly as ``TextIOWrapper`` does."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
    payload = b"a\r\nb\rc\n"
    with io.TextIOWrapper(
        io.BytesIO(payload), encoding="utf-8", errors="strict"
    ) as reference:
        expected = reference.read()
    decoded = capture._stdin_decoder(_Stream()).decode(payload)
    assert decoded == expected, "Windows capture decoding diverged from TextIOWrapper"


def test_decoder_preserves_newlines_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSIX decoding keeps carriage returns, matching ``TextIOWrapper``."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", False)
    decoded = capture._stdin_decoder(_Stream()).decode(b"a\r\nb\rc\n")
    assert decoded == "a\r\nb\rc\n", "POSIX decoding must preserve carriage returns"


def test_decoder_uses_stream_encoding_for_non_ascii() -> None:
    """Non-ASCII text decodes with the stream's declared encoding."""
    decoded = capture._stdin_decoder(_Stream()).decode("\N{EURO SIGN}".encode())
    assert decoded == "\N{EURO SIGN}", (
        "non-ASCII text did not decode with the stream encoding"
    )


def test_poll_error_propagates() -> None:
    """A failure from the poller surfaces as the original exception."""
    stream = _DescriptorStream(0)
    clock = _Clock()
    context = capture._PollContext(0, _ErrorPoller(), 1.0, 1.0, clock)
    plan = capture._Plan("poll", descriptor=0, poller=_ErrorPoller())
    with pytest.raises(_PollerOSError):
        capture._read_raw_polled(
            capture._Capture(stream, 1.0, 1.0, clock, plan), context
        )


def test_buffered_strategy_reads_through_eof() -> None:
    """A stream exposing a buffered reader uses the buffered strategy."""
    with _open_pipe_stream(b"buffered payload") as stream:
        read_chunk = getattr(getattr(stream, "buffer", None), "read1", None)
        assert callable(read_chunk), (
            "the fixture must expose a buffered reader for this strategy"
        )
        assert _read(stream, 5.0, _Clock()) == "buffered payload", (
            "the buffered strategy should yield the piped text"
        )


# --- Regular-file strategy --------------------------------------------------


def test_regular_file_strategy_is_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regular file behind a pollable descriptor reads through the worker."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", False)
    path = tmp_path / "input.txt"
    path.write_text("file data", encoding="utf-8")
    with path.open(encoding="utf-8") as stream:
        assert capture._plan_stdin(stream).strategy == "regular", (
            "a regular file should read through the worker"
        )
        assert _read(stream, 5.0, _Clock()) == "file data", (
            "the regular-file strategy should return the file contents"
        )


def test_regular_file_timeout_reports_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stalled regular-file read expires at the shared deadline."""
    stall = threading.Event()

    def stalled_read(_descriptor: int, _size: int) -> bytes:
        """Block until released, then report EOF as ``os.read`` would.

        Returns
        -------
        bytes
            Empty bytes, the signal a reader sees at end-of-file.
        """
        stall.wait(5.0)
        return b""

    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(capture.os, "read", stalled_read)
    path = tmp_path / "input.txt"
    path.write_text("never read", encoding="utf-8")
    try:
        with path.open(encoding="utf-8") as stream, pytest.raises(StdinTimeoutError):
            _read(stream, 0.05, _Clock())
    finally:
        stall.set()


# --- Worker completion boundary ---------------------------------------------


def _fallback_capture(clock: _Clock) -> capture._Capture:
    """Build a capture that reads a no-descriptor stream on a worker.

    Returns
    -------
    capture._Capture
        Capture whose plan selects the fallback worker.
    """
    return capture._Capture(
        stream=_Stream("boundary"),
        timeout=1.0,
        deadline=1.0,
        clock=clock,
        plan=capture._Plan("fallback"),
    )


def _regular_file_capture(clock: _Clock) -> tuple[capture._Capture, int]:
    """Build a capture over a pipe primed with data and already at EOF.

    Returns
    -------
    tuple[capture._Capture, int]
        The capture plus the read descriptor the test must close.
    """
    read_fd, write_fd = os.pipe()
    os.write(write_fd, b"boundary")
    os.close(write_fd)
    return (
        capture._Capture(
            stream=_DescriptorStream(read_fd),
            timeout=1.0,
            deadline=1.0,
            clock=clock,
            plan=capture._Plan("regular", descriptor=read_fd),
        ),
        read_fd,
    )


@pytest.mark.parametrize("strategy", ["fallback", "regular"])
@pytest.mark.parametrize(
    ("advance", "is_late"),
    [(0.5, False), (1.0, True), (2.0, True)],
    ids=["before", "at", "after"],
)
def test_worker_strategies_reject_late_completion(
    monkeypatch: pytest.MonkeyPatch,
    strategy: str,
    advance: float,
    *,
    is_late: bool,
) -> None:
    """A worker result observed at or after the deadline is a timeout.

    The waiting thread can resume after the deadline even though the stream
    finished in time, so each worker strategy must recheck the clock rather
    than trust that a set event means an in-budget read. Completion exactly at
    the deadline is late, matching the polled reader.
    """
    clock = _Clock()
    _install_completion_clock(monkeypatch, clock, advance)
    read_fd: int | None = None
    if strategy == "fallback":
        worker_capture = _fallback_capture(clock)
        read_worker = capture._read_fallback
    else:
        worker_capture, read_fd = _regular_file_capture(clock)
        read_worker = capture._read_regular_file
    try:
        if is_late:
            with pytest.raises(StdinTimeoutError):
                read_worker(worker_capture)
        else:
            assert read_worker(worker_capture) == "boundary", (
                "an in-budget worker result should be returned"
            )
    finally:
        if read_fd is not None:
            os.close(read_fd)


# --- Strategy planning ------------------------------------------------------


def test_plan_selects_fallback_for_unsupported_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream with no descriptor has no pollable strategy."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", False)
    assert capture._plan_stdin(_NoFileno()).strategy == "fallback", (
        "a descriptorless stream should use the fallback worker"
    )


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_plan_selects_poll_for_a_pipe() -> None:
    """A pipe descriptor is polled rather than read blindly."""
    read_fd, write_fd = os.pipe()
    try:
        plan = capture._plan_stdin(_DescriptorStream(read_fd))
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert plan.strategy == "poll", "a pipe should be polled"
    assert plan.descriptor == read_fd, "the wrong descriptor was recorded"


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_plan_treats_an_unstattable_descriptor_as_pollable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A descriptor whose status cannot be read is polled, not worked."""

    def reject(_descriptor: int) -> os.stat_result:
        msg = "stat failed"
        raise OSError(msg)

    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(capture.os, "fstat", reject)
    try:
        plan = capture._plan_stdin(_DescriptorStream(read_fd))
    finally:
        os.close(read_fd)
        os.close(write_fd)
    assert plan.strategy == "poll", "an unstattable descriptor should be polled"
