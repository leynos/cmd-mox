"""Unit tests for the Windows handle stdin capture strategy."""

from __future__ import annotations

import os
import threading
import types
import typing as typ

import pytest

from cmd_mox import _stdin_capture as capture
from cmd_mox.errors import StdinTimeoutError
from cmd_mox.unittests._stdin_capture_fakes import (
    _Clock,
    _DescriptorStream,
    _Stream,
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

    assert capture.read_stdin(_Stream(), 5.0, clock=_Clock()) == "windows", (
        "the Windows strategy should return the decoded handle data"
    )
    assert read_handles == [_HANDLE, _HANDLE, _HANDLE], "the wrong handle was read"
    assert harness.cancelled == [], "a completed read must not be cancelled"


def test_windows_handle_reports_unexpected_errors_as_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-broken-pipe ``pywintypes`` error reaches the caller as ``OSError``.

    ``pywintypes.error`` is not an ``OSError``, so re-raising it verbatim would
    bypass the shim's ``(OSError, UnicodeError, ValueError, RuntimeError)``
    stdin handler and surface as an unhandled traceback instead of the bounded
    ``stdin read failed`` diagnostic.
    """

    def read_file(_handle: object, _size: int) -> tuple[int, bytes]:
        raise _FakeWinError(
            _FakeWinError.ACCESS_DENIED_MESSAGE, _FakeWinError.ACCESS_DENIED
        )

    _install_windows_harness(monkeypatch, _Win32Harness(read_file))
    with pytest.raises(OSError, match="stdin handle read failed") as excinfo:
        capture.read_stdin(_Stream(), 5.0, clock=_Clock())

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
    assert harness.cancelled, "the read was never cancelled"
    assert len(harness.closed) == len(harness.opened), (
        "a thread handle was opened and never closed"
    )
    assert all(args[2] is not None for args in harness.opened), (
        "no native thread was targeted"
    )
    assert excinfo.value.worker_alive is True, "the blocked worker should live"


def test_windows_handle_completes_without_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handle read that reaches EOF before the deadline is not cancelled."""
    harness = _Win32Harness(lambda _handle, _size: (0, b""))
    _install_windows_harness(monkeypatch, harness)

    assert capture.read_stdin(_Stream(), 5.0, clock=_Clock()) == "", (
        "an immediate EOF should capture no text"
    )
    assert harness.cancelled == [], "a completed read must not be cancelled"
    assert harness.closed == [], "no thread handle should be opened"


def test_windows_without_a_handle_uses_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows stream whose handle cannot be resolved reads directly."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(capture, "_stdin_handle", lambda _stream: None)
    stream = _Stream("fallback on windows")

    assert capture.read_stdin(stream, 5.0, clock=_Clock()) == "fallback on windows", (
        "a missing handle should fall back to a direct read"
    )
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
    assert capture._stdin_handle(stream) is None, (
        "an unusable handle must be reported as missing"
    )


def test_windows_missing_pywin32_uses_the_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows host without pywin32 degrades to the tracked fallback."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(capture, "_stdin_handle", lambda _stream: _HANDLE)
    monkeypatch.setattr(capture, "_windows_modules", lambda: None)
    stream = _Stream("no pywin32")

    assert capture.read_stdin(stream, 5.0, clock=_Clock()) == "no pywin32", (
        "a host without pywin32 should still capture stdin"
    )
    assert stream.read_calls == 1, "the fallback should read exactly once"


@pytest.mark.skipif(os.name == "nt", reason="asserts the non-Windows branch")
def test_windows_modules_are_inert_off_windows() -> None:
    """``_windows_modules`` returns nothing on a non-Windows host."""
    assert capture._windows_modules() is None, (
        "the Windows modules must stay inert away from Windows"
    )


# --- Strategy planning ------------------------------------------------------


def test_plan_selects_win32_for_a_resolved_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows resolves a handle before considering descriptors."""
    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(capture, "_stdin_handle", lambda _stream: _HANDLE)
    assert capture._plan_stdin(_Stream()).strategy == "win32", (
        "a resolved handle should select the Windows strategy"
    )
