"""Unit tests for the Windows handle stdin capture strategy."""

from __future__ import annotations

import threading
import types
import typing as typ

import pytest

from cmd_mox import _stdin_capture as capture
from cmd_mox.errors import StdinTimeoutError
from cmd_mox.unittests._stdin_capture_fakes import (
    _Clock,
    _Stream,
    _system_with_completion_clock,
    _SystemDouble,
    _WindowsSystemDouble,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# --- Windows strategy -------------------------------------------------------

_HANDLE = object()


class _FakeWinError(Exception):
    """Stand-in for ``pywintypes.error``."""

    ACCESS_DENIED: typ.ClassVar[int] = 5
    ACCESS_DENIED_MESSAGE: typ.ClassVar[str] = "access denied"

    def __init__(self, message: str, winerror: int = 0) -> None:
        """Store the Windows error code alongside the message."""
        super().__init__(message)
        self.winerror = winerror


class _Win32Harness:
    """Fake pywin32 modules recording every cancellation-related call."""

    def __init__(self, read_file: cabc.Callable[..., tuple[int, bytes]]) -> None:
        """Build fake pywin32 modules that record cancellation and close calls."""
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


def _windows_system(harness: _Win32Harness) -> _SystemDouble:
    """Return an injected system that routes Windows operations through *harness*.

    Returns
    -------
    _SystemDouble
        System naming the Windows strategy and resolving the harness modules.
    """
    return _SystemDouble(
        windows=_WindowsSystemDouble(
            windows_flag=True,
            handle_override=_HANDLE,
            modules_override=harness.modules(),
        )
    )


def test_windows_handle_reads_until_broken_pipe() -> None:
    """A Windows handle read decodes chunks and stops at a closed pipe."""
    chunks = iter([b"win", b"dows"])
    read_handles: list[object] = []

    def read_file(handle: object, _size: int) -> tuple[int, bytes]:
        """Return the next chunk, then raise a broken-pipe error at end of data."""
        read_handles.append(handle)
        try:
            return 0, next(chunks)
        except StopIteration:
            raise _FakeWinError("closed", 109) from None

    harness = _Win32Harness(read_file)
    system = _windows_system(harness)

    captured = capture.read_stdin(_Stream(), 5.0, clock=_Clock(), system=system)

    assert captured == "windows", (
        "the Windows strategy should return the decoded handle data"
    )
    assert read_handles == [_HANDLE, _HANDLE, _HANDLE], "the wrong handle was read"
    assert harness.cancelled == [], "a completed read must not be cancelled"


def test_windows_handle_reports_unexpected_errors_as_oserror() -> None:
    """A non-broken-pipe ``pywintypes`` error reaches the caller as ``OSError``.

    ``pywintypes.error`` is not an ``OSError``, so re-raising it verbatim would
    bypass the shim's ``(OSError, UnicodeError, ValueError, RuntimeError)``
    stdin handler and surface as an unhandled traceback instead of the bounded
    ``stdin read failed`` diagnostic.
    """

    def read_file(_handle: object, _size: int) -> tuple[int, bytes]:
        """Raise an access-denied error from the native read."""
        raise _FakeWinError(
            _FakeWinError.ACCESS_DENIED_MESSAGE, _FakeWinError.ACCESS_DENIED
        )

    system = _windows_system(_Win32Harness(read_file))
    with pytest.raises(OSError, match="stdin handle read failed") as excinfo:
        capture.read_stdin(_Stream(), 5.0, clock=_Clock(), system=system)

    assert excinfo.value.errno == _FakeWinError.ACCESS_DENIED, (
        "the Windows error code was not preserved"
    )
    assert isinstance(excinfo.value.__cause__, _FakeWinError), (
        "the original pywin32 failure was not preserved as the cause"
    )
    assert _FakeWinError.ACCESS_DENIED_MESSAGE not in str(excinfo.value), (
        "the pywin32 message leaked into the captured error"
    )


def test_windows_handle_timeout_cancels_and_closes_one_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired handle read cancels the worker and leaks no thread handle.

    The number of cancellation attempts is not pinned: the shared helper
    re-issues ``CancelSynchronousIo`` while the worker survives, because a
    request landing between two ``ReadFile`` calls is swallowed. Each attempt
    opens and closes its own thread handle, so the invariant is that every
    handle opened was closed again.
    """
    release = threading.Event()
    started = threading.Event()

    def read_file(_handle: object, _size: int) -> tuple[int, bytes]:
        """Signal that the read started, then block until released."""
        started.set()
        release.wait(5.0)
        return 0, b""

    harness = _Win32Harness(read_file)
    system = _windows_system(harness)
    monkeypatch.setattr(capture, "_CANCEL_GRACE", 0.01)

    try:
        with pytest.raises(StdinTimeoutError) as excinfo:
            capture.read_stdin(_Stream(), 0.05, clock=_Clock(), system=system)
    finally:
        release.set()

    assert started.is_set(), "the handle read never began"
    assert harness.cancelled, "the read was never cancelled"
    assert len(harness.closed) == len(harness.opened), (
        "a thread handle was opened and never closed"
    )
    assert all(args[2] is not None for args in harness.opened), (
        "no native thread was targeted"
    )
    assert excinfo.value.worker_alive is True, "the blocked worker should live"


def test_windows_cancel_lets_the_worker_exit_within_the_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful cancel ends the worker without a forced process exit.

    ``tests/test_shim_stdin_timeout.py`` proves the shim shuts down while its
    stdin writer stays open, but either the native cancellation or the
    tracked-worker ``os._exit`` fallback can satisfy it. This test isolates the
    native path: the cancellation releases the blocked read, the worker exits
    inside the grace, and the timeout therefore reports ``worker_alive`` as
    false.
    """
    started = threading.Event()
    release = threading.Event()

    def read_file(_handle: object, _size: int) -> tuple[int, bytes]:
        """Signal that the read started, then block until released."""
        started.set()
        release.wait(5.0)
        return 0, b""

    harness = _Win32Harness(read_file)

    def cancel(_handle: object) -> None:
        """Record the cancelled handle and release the blocked read."""
        harness.cancelled.append(_handle)
        release.set()

    harness.win32file.CancelSynchronousIo = cancel
    system = _windows_system(harness)
    monkeypatch.setattr(capture, "_CANCEL_GRACE", 5.0)

    try:
        with pytest.raises(StdinTimeoutError) as excinfo:
            capture.read_stdin(_Stream(), 0.05, clock=_Clock(), system=system)
    finally:
        release.set()

    assert started.is_set(), "the handle read never began"
    assert harness.cancelled, "the read was never cancelled"
    assert excinfo.value.worker_alive is False, (
        "the worker did not exit during the cancellation grace"
    )


@pytest.mark.parametrize(
    ("advance", "is_late"),
    [(0.5, False), (1.0, True), (2.0, True)],
    ids=["before", "at", "after"],
)
def test_windows_worker_rejects_late_completion(
    advance: float, *, is_late: bool
) -> None:
    """A handle read observed at or after the deadline is a timeout.

    The Windows worker shares the bounded wait with the other worker
    strategies, so a result that only becomes observable at or after the
    deadline must not be returned as if it had arrived in time.
    """
    clock = _Clock()
    harness = _Win32Harness(lambda _handle, _size: (0, b""))
    base = _windows_system(harness)
    system = _system_with_completion_clock(clock, advance, windows=base.windows)
    worker_capture = capture._Capture(
        stream=_Stream(),
        timeout=1.0,
        deadline=1.0,
        clock=clock,
        plan=capture._Plan("win32", handle=_HANDLE),
        system=system,
    )

    if is_late:
        with pytest.raises(StdinTimeoutError):
            capture._read_win32(worker_capture)
    else:
        assert capture._read_win32(worker_capture) == "", (
            "an in-budget handle read should return its text"
        )


def test_windows_handle_completes_without_cancellation() -> None:
    """A handle read that reaches EOF before the deadline is not cancelled."""
    harness = _Win32Harness(lambda _handle, _size: (0, b""))
    system = _windows_system(harness)

    assert capture.read_stdin(_Stream(), 5.0, clock=_Clock(), system=system) == "", (
        "an immediate EOF should capture no text"
    )
    assert harness.cancelled == [], "a completed read must not be cancelled"
    assert harness.closed == [], "no thread handle should be opened"


def test_windows_without_a_handle_uses_the_fallback() -> None:
    """A Windows stream whose handle cannot be resolved reads directly."""
    system = _SystemDouble(
        windows=_WindowsSystemDouble(windows_flag=True, handle_override=None)
    )
    stream = _Stream("fallback on windows")

    assert (
        capture.read_stdin(stream, 5.0, clock=_Clock(), system=system)
        == "fallback on windows"
    ), "a missing handle should fall back to a direct read"
    assert stream.read_calls == 1, "the fallback should read exactly once"


def test_windows_missing_pywin32_uses_the_fallback() -> None:
    """A Windows host without pywin32 degrades to the tracked fallback."""
    system = _SystemDouble(
        windows=_WindowsSystemDouble(
            windows_flag=True,
            handle_override=_HANDLE,
            modules_override=None,
        )
    )
    stream = _Stream("no pywin32")

    captured = capture.read_stdin(stream, 5.0, clock=_Clock(), system=system)
    assert captured == "no pywin32", "a host without pywin32 should still capture stdin"
    assert stream.read_calls == 1, "the fallback should read exactly once"


# --- Strategy planning ------------------------------------------------------


def test_plan_selects_win32_for_a_resolved_handle() -> None:
    """Windows resolves a handle before considering descriptors."""
    system = _SystemDouble(
        windows=_WindowsSystemDouble(
            windows_flag=True,
            handle_override=_HANDLE,
            modules_override=(object(), object(), object()),
        )
    )
    assert capture._plan_stdin(_Stream(), system=system).strategy == "win32", (
        "a resolved handle should select the Windows strategy"
    )


def test_plan_falls_back_when_the_win32_modules_are_missing() -> None:
    """A plan must name the strategy that will actually run.

    ``_read_win32`` degrades to the tracked fallback when pywin32 is absent, so
    the selected plan must not claim the Windows strategy for that stream.
    """
    system = _SystemDouble(
        windows=_WindowsSystemDouble(
            windows_flag=True,
            handle_override=_HANDLE,
            modules_override=None,
        )
    )

    assert capture._plan_stdin(_Stream(), system=system).strategy == "fallback", (
        "a missing pywin32 must not be planned as the Windows strategy"
    )
