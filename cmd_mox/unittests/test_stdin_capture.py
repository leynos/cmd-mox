"""Unit tests for the bounded stdin capture strategies."""

from __future__ import annotations

import io
import os
import threading
import types
import typing as typ

import pytest

from cmd_mox import _stdin_capture as capture
from cmd_mox.errors import StdinTimeoutError

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path


class _Clock:
    """Mutable monotonic clock advanced explicitly by each test."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        """Return the current fake time.

        Returns
        -------
        float
            The time last assigned to :attr:`now`.
        """
        return self.now


class _Stream:
    """Non-tty text stream with no descriptor and no buffered reader."""

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, data: str = "", *, is_tty: bool = False) -> None:
        self.data = data
        self.read_calls = 0
        self._is_tty = is_tty

    def isatty(self) -> bool:
        """Report the terminal status configured for this stream.

        Returns
        -------
        bool
            ``True`` when the double simulates a terminal.
        """
        return self._is_tty

    def read(self) -> str:
        """Return the configured text and count the read.

        Returns
        -------
        str
            The text supplied when the double was created.
        """
        self.read_calls += 1
        return self.data


class _NoFileno(_Stream):
    """Stream whose ``fileno`` reports that no descriptor exists."""

    def fileno(self) -> int:
        """Reject descriptor access the way ``StringIO`` does.

        Raises
        ------
        io.UnsupportedOperation
            Always, so callers take the missing-descriptor path.
        """
        msg = "no descriptor"
        raise io.UnsupportedOperation(msg)


class _BlockingStream(_Stream):
    """Stream whose read blocks until *release* is set."""

    def __init__(self, data: str) -> None:
        super().__init__(data)
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def read(self) -> str:
        """Block until released, then return the configured text.

        Returns
        -------
        str
            The text supplied when the double was created.
        """
        self.started.set()
        try:
            self.release.wait(5.0)
            return self.data
        finally:
            self.finished.set()


class _DescriptorStream(_Stream):
    """Stream exposing an explicit descriptor and optional buffer."""

    def __init__(self, descriptor: int, *, data: str = "") -> None:
        super().__init__(data)
        self._descriptor = descriptor

    def fileno(self) -> int:
        """Return the descriptor supplied to this double.

        Returns
        -------
        int
            The descriptor configured for the double.
        """
        return self._descriptor


class _PollerOSError(OSError):
    """I/O failure raised by a scripted poller."""


class _ErrorPoller:
    """Poller whose registration succeeds but whose wait fails."""

    def poll(self, timeout: int, /) -> list[tuple[int, int]]:
        """Fail the readiness wait.

        Raises
        ------
        _PollerOSError
            Always, so callers can assert the failure propagates.
        """
        msg = "blocked poll failed"
        raise _PollerOSError(msg)


class _StreamError(RuntimeError):
    """Failure raised by a test stream."""


def _read(stream: object, timeout: float, clock: _Clock) -> str:
    """Capture stdin for a test using an explicit clock.

    Returns
    -------
    str
        The captured text.
    """
    return capture.read_stdin(
        typ.cast("capture._StdinStream", stream), timeout, clock=clock
    )


def _open_pipe_stream(payload: bytes) -> typ.IO[str]:
    """Return a text stream over a pipe already primed with *payload*.

    Returns
    -------
    typ.IO[str]
        A text stream whose writer has been closed, so it reads through EOF.
    """
    read_fd, write_fd = os.pipe()
    os.write(write_fd, payload)
    os.close(write_fd)
    return os.fdopen(read_fd, encoding="utf-8", errors="strict")


# --- TTY and in-memory fast paths -------------------------------------------


def test_tty_stdin_is_not_read() -> None:
    """A terminal stream is returned as empty without a read call."""
    stream = _Stream("unused", is_tty=True)
    assert _read(stream, 1.0, _Clock()) == ""
    assert stream.read_calls == 0, "a tty must not be read"


def test_string_io_is_read_without_a_worker() -> None:
    """An in-memory stream is read directly, even at a zero deadline."""
    assert _read(io.StringIO("payload"), 0.0, _Clock()) == "payload"


# --- Fallback strategy ------------------------------------------------------


def test_missing_descriptor_reads_through_a_worker() -> None:
    """A stream without ``fileno`` is read on the fallback worker."""
    stream = _NoFileno("fallback data")
    assert _read(stream, 1.0, _Clock()) == "fallback data"
    assert stream.read_calls == 1, "the fallback should read exactly once"


def test_unpollable_descriptor_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """A descriptor both pollers reject is read through the fallback worker."""
    monkeypatch.setattr(capture, "_create_stdin_poller", lambda _descriptor: None)
    stream = _DescriptorStream(0, data="unpollable")
    assert _read(stream, 1.0, _Clock()) == "unpollable"
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
    assert _read(_Stream("prompt data"), 1.0, _Clock()) == "prompt data"


# --- Polled strategy --------------------------------------------------------


def test_expiry_exactly_at_the_deadline_raises() -> None:
    """A deadline that has just been reached is an expiry, not a wait."""
    with pytest.raises(StdinTimeoutError):
        _read(_DescriptorStream(0), 0.0, _Clock())


def test_polled_read_returns_data_before_the_deadline() -> None:
    """A pollable descriptor yields the text queued before the deadline."""
    with _open_pipe_stream(b"polled payload") as stream:
        assert _read(stream, 5.0, _Clock()) == "polled payload"


def test_polled_read_does_not_advance_the_injected_clock() -> None:
    """The polled strategy consults the injected clock, not wall time."""
    clock = _Clock()
    with _open_pipe_stream(b"injected") as stream:
        assert _read(stream, 1.0, clock) == "injected"
    assert clock.now == pytest.approx(0.0), "a fast read must not advance the clock"


def test_polled_read_preserves_newlines_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Piped carriage returns survive on POSIX, matching ``TextIOWrapper``."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", False)
    with _open_pipe_stream(b"first\r\nsecond\rthird") as stream:
        assert _read(stream, 5.0, _Clock()) == "first\r\nsecond\rthird"


def test_decoder_matches_text_wrapper_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows decoding translates newlines exactly as ``TextIOWrapper`` does."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
    assert capture._stdin_decoder(_Stream()).decode(b"a\r\nb\rc\n") == "a\nb\nc\n"


def test_decoder_preserves_newlines_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POSIX decoding keeps carriage returns, matching ``TextIOWrapper``."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", False)
    assert capture._stdin_decoder(_Stream()).decode(b"a\r\nb\rc\n") == "a\r\nb\rc\n"


def test_decoder_uses_stream_encoding_for_non_ascii() -> None:
    """Non-ASCII text decodes with the stream's declared encoding."""
    assert capture._stdin_decoder(_Stream()).decode("\N{EURO SIGN}".encode()) == (
        "\N{EURO SIGN}"
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

    class _Buffered(io.TextIOWrapper):
        pass

    with _open_pipe_stream(b"buffered payload") as stream:
        assert isinstance(stream, _Buffered) or hasattr(stream, "buffer")
        assert _read(stream, 5.0, _Clock()) == "buffered payload"


# --- Regular-file strategy --------------------------------------------------


def test_regular_file_strategy_is_selected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regular file behind a pollable descriptor reads through the worker."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", False)
    path = tmp_path / "input.txt"
    path.write_text("file data", encoding="utf-8")
    with path.open(encoding="utf-8") as stream:
        assert capture._plan_stdin(stream).strategy == "regular"
        assert _read(stream, 5.0, _Clock()) == "file data"


def test_regular_file_timeout_reports_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stalled regular-file read expires at the shared deadline."""
    stall = threading.Event()
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(capture.os, "read", lambda _descriptor, _size: stall.wait(5.0))
    path = tmp_path / "input.txt"
    path.write_text("never read", encoding="utf-8")
    try:
        with path.open(encoding="utf-8") as stream, pytest.raises(StdinTimeoutError):
            _read(stream, 0.05, _Clock())
    finally:
        stall.set()


# --- Windows strategy -------------------------------------------------------

_HANDLE = object()


class _FakeWinError(Exception):
    """Stand-in for ``pywintypes.error``."""

    ACCESS_DENIED: typ.ClassVar[int] = 5
    ACCESS_DENIED_MESSAGE: typ.ClassVar[str] = "access denied"

    def __init__(self, message: str, winerror: int = 0) -> None:
        super().__init__(message)
        self.winerror = winerror


class _Win32Harness:
    """Fake pywin32 modules recording every cancellation-related call."""

    def __init__(self, read_file: cabc.Callable[..., tuple[int, bytes]]) -> None:
        self.cancelled: list[object] = []
        self.closed: list[object] = []
        self.opened: list[tuple[object, ...]] = []
        self.win32file = types.SimpleNamespace(
            ReadFile=read_file,
            CancelSynchronousIo=self.cancelled.append,
            CloseHandle=self.closed.append,
        )
        self.win32api = types.SimpleNamespace(OpenThread=self._open_thread)
        self.pywintypes = types.SimpleNamespace(error=_FakeWinError)

    def _open_thread(self, *args: object) -> object:
        """Record the ``OpenThread`` call and return a fresh handle.

        Returns
        -------
        object
            A sentinel standing in for the native thread handle.
        """
        self.opened.append(args)
        return object()

    def modules(self) -> tuple[object, object, object]:
        """Return the module triple the capture expects.

        Returns
        -------
        tuple[object, object, object]
            ``(win32file, win32api, pywintypes)`` stand-ins.
        """
        return (self.win32file, self.win32api, self.pywintypes)


def _install_windows_harness(
    monkeypatch: pytest.MonkeyPatch, harness: _Win32Harness
) -> None:
    """Route the capture module's Windows seams through *harness*."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(capture, "_stdin_handle", lambda _stream: _HANDLE)
    monkeypatch.setattr(capture, "_windows_modules", harness.modules)


def test_windows_handle_reads_until_broken_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows handle read decodes chunks and stops at a closed pipe."""
    chunks = iter([b"win", b"dows"])
    read_handles: list[object] = []

    def read_file(handle: object, _size: int) -> tuple[int, bytes]:
        read_handles.append(handle)
        try:
            return 0, next(chunks)
        except StopIteration:
            raise _FakeWinError("closed", 109) from None

    harness = _Win32Harness(read_file)
    _install_windows_harness(monkeypatch, harness)

    assert capture.read_stdin(_Stream(), 5.0, clock=_Clock()) == "windows"
    assert read_handles == [_HANDLE, _HANDLE, _HANDLE], "the wrong handle was read"
    assert harness.cancelled == [], "a completed read must not be cancelled"


def test_windows_handle_reraises_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-broken-pipe ``pywintypes`` error propagates to the caller."""

    def read_file(_handle: object, _size: int) -> tuple[int, bytes]:
        raise _FakeWinError(
            _FakeWinError.ACCESS_DENIED_MESSAGE, _FakeWinError.ACCESS_DENIED
        )

    _install_windows_harness(monkeypatch, _Win32Harness(read_file))
    with pytest.raises(_FakeWinError, match="access denied"):
        capture.read_stdin(_Stream(), 5.0, clock=_Clock())


def test_windows_handle_timeout_cancels_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired handle read cancels the worker and closes one handle."""
    release = threading.Event()
    started = threading.Event()

    def read_file(_handle: object, _size: int) -> tuple[int, bytes]:
        started.set()
        release.wait(5.0)
        return 0, b""

    harness = _Win32Harness(read_file)
    _install_windows_harness(monkeypatch, harness)
    monkeypatch.setattr(capture, "_CANCEL_GRACE", 0.01)

    try:
        with pytest.raises(StdinTimeoutError) as excinfo:
            capture.read_stdin(_Stream(), 0.05, clock=_Clock())
    finally:
        release.set()

    assert started.is_set(), "the handle read never began"
    assert len(harness.cancelled) == 1, "the read was not cancelled exactly once"
    assert len(harness.closed) == 1, "the thread handle was not closed exactly once"
    assert len(harness.opened) == 1, "the thread handle was not opened exactly once"
    assert harness.opened[0][2] is not None, "no native thread was targeted"
    assert excinfo.value.worker_alive is True, "the blocked worker should live"


def test_windows_handle_completes_without_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handle read that reaches EOF before the deadline is not cancelled."""
    harness = _Win32Harness(lambda _handle, _size: (0, b""))
    _install_windows_harness(monkeypatch, harness)

    assert capture.read_stdin(_Stream(), 5.0, clock=_Clock()) == ""
    assert harness.cancelled == [], "a completed read must not be cancelled"
    assert harness.closed == [], "no thread handle should be opened"


def test_windows_without_a_handle_uses_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows stream whose handle cannot be resolved reads directly."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(capture, "_stdin_handle", lambda _stream: None)
    stream = _Stream("fallback on windows")

    assert capture.read_stdin(stream, 5.0, clock=_Clock()) == "fallback on windows"
    assert stream.read_calls == 1, "the fallback should read exactly once"


def test_windows_handle_resolution_falls_back_when_msvcrt_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``get_osfhandle`` failure is reported as a missing handle."""

    def reject(_descriptor: int) -> object:
        msg = "no console"
        raise OSError(msg)

    monkeypatch.setitem(
        typ.cast("dict[str, object]", __import__("sys").modules),
        "msvcrt",
        types.SimpleNamespace(get_osfhandle=reject),
    )
    stream = _DescriptorStream(0)
    assert capture._stdin_handle(stream) is None


def test_windows_missing_pywin32_uses_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows host without pywin32 degrades to the tracked fallback."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(capture, "_stdin_handle", lambda _stream: _HANDLE)
    monkeypatch.setattr(capture, "_windows_modules", lambda: None)
    stream = _Stream("no pywin32")

    assert capture.read_stdin(stream, 5.0, clock=_Clock()) == "no pywin32"
    assert stream.read_calls == 1, "the fallback should read exactly once"


def test_windows_modules_are_inert_off_windows() -> None:
    """``_windows_modules`` returns nothing on a non-Windows host."""
    assert capture._windows_modules() is None


# --- Strategy planning ------------------------------------------------------


def test_plan_selects_win32_for_a_resolved_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows resolves a handle before considering descriptors."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(capture, "_stdin_handle", lambda _stream: _HANDLE)
    assert capture._plan_stdin(_Stream()).strategy == "win32"


def test_plan_selects_fallback_for_unsupported_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stream with no descriptor has no pollable strategy."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", False)
    assert capture._plan_stdin(_NoFileno()).strategy == "fallback"


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
