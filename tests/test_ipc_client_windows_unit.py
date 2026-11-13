"""Unit tests for Windows-specific IPC client helpers."""

from __future__ import annotations

import os
import pathlib
import time
import types
import typing as typ

import pytest

from cmd_mox.ipc import client, windows
from cmd_mox.ipc.client import RetryConfig, _ConnectionContext

_TIMEOUT_MESSAGE = "IPC client operation timed out"

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@pytest.fixture(autouse=True)
def _patch_windows_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub Windows-only modules so the named-pipe client runs on any platform."""
    monkeypatch.setattr("cmd_mox._path_utils.IS_WINDOWS", True)
    monkeypatch.setattr(
        client,
        "pywintypes",
        types.SimpleNamespace(error=_DummyPipeError),
    )
    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(
            CloseHandle=lambda _handle: None,
            WriteFile=lambda *_args, **_kwargs: None,
            FlushFileBuffers=lambda *_args, **_kwargs: None,
        ),
    )
    monkeypatch.setattr(
        client,
        "win32pipe",
        types.SimpleNamespace(WaitNamedPipe=lambda *_args, **_kwargs: None),
    )


class _DummyPipeError(Exception):
    """Stand-in for ``pywintypes.error`` carrying a Windows error code."""

    def __init__(self, winerror: int) -> None:
        """Store the Windows error code alongside the exception message."""
        super().__init__(winerror)
        self.winerror = winerror


def test_connect_pipe_with_retries_eventually_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure named pipe connections retry when the backend is busy."""
    attempts = {"count": 0}

    def fake_create(_pipe_name: pathlib.Path, *_args: object) -> str:
        """Report a busy pipe on the first attempt, then return a handle."""
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise _DummyPipeError(windows.ERROR_PIPE_BUSY)
        return "HANDLE"

    monkeypatch.setattr(client, "_create_pipe_handle", fake_create)
    monkeypatch.setattr(
        client,
        "_wait_for_pipe_availability",
        lambda *_args, **_kwargs: None,
    )

    handle = client._connect_pipe_with_retries(
        pathlib.Path("pipe"),
        _ConnectionContext(
            timeout=0.1,
            retry_config=RetryConfig(retries=3, backoff=0.0, jitter=0.0),
        ),
    )

    assert handle == "HANDLE", "Assertion failed"
    assert attempts["count"] == 2, "Assertion failed"


def test_connect_pipe_with_retries_raises_after_non_retryable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify non-retryable errors bubble up immediately."""

    def fake_create(_pipe_name: pathlib.Path, *_args: object) -> str:
        """Raise a non-retryable pipe error on every call."""
        raise _DummyPipeError(windows.ERROR_NO_DATA)

    monkeypatch.setattr(client, "_create_pipe_handle", fake_create)

    with pytest.raises(_DummyPipeError):
        client._connect_pipe_with_retries(
            pathlib.Path("pipe"),
            _ConnectionContext(
                timeout=0.1,
                retry_config=RetryConfig(retries=2, backoff=0.0, jitter=0.0),
            ),
        )


def test_send_pipe_request_writes_and_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_send_pipe_request`` should write payloads and read responses."""
    handle_log: list[str] = []

    class _FakeHandle:
        """Record whether the fake pipe handle has been closed."""

        def __init__(self) -> None:
            """Start with the handle open."""
            self.closed = False

    fake_handle = _FakeHandle()

    monkeypatch.setattr(
        client,
        "_connect_pipe_with_retries",
        lambda *_args, **_kwargs: fake_handle,
    )
    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(
            WriteFile=lambda handle, payload: handle_log.append(
                payload.decode("utf-8")
            ),
            FlushFileBuffers=lambda *_args, **_kwargs: None,
            CloseHandle=lambda handle: setattr(handle, "closed", True),
            ReadFile=lambda *_args, **_kwargs: (0, b"{}"),
        ),
    )

    response = client._send_pipe_request(
        pathlib.Path("pipe"),
        b"{}",
        _ConnectionContext(timeout=0.1, retry_config=RetryConfig()),
    )

    assert response == b"{}", "Assertion failed"
    assert handle_log == ["{}"], "Assertion failed"
    assert fake_handle.closed is True, "Assertion failed"


def test_send_pipe_request_closes_handle_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_send_pipe_request`` should close handles when timeouts surface."""

    class _FakeHandle:
        """Record whether the fake pipe handle has been closed."""

        def __init__(self) -> None:
            """Start with the handle open."""
            self.closed = False

    fake_handle = _FakeHandle()

    def fake_close(handle: _FakeHandle) -> None:
        """Mark the fake handle as closed."""
        handle.closed = True

    monkeypatch.setattr(
        client,
        "_connect_pipe_with_retries",
        lambda *_args, **_kwargs: fake_handle,
    )
    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(CloseHandle=fake_close),
    )

    call_count = {"value": 0}

    def fake_run(
        func: cabc.Callable[[], object],
        *,
        deadline: float,
        cancel: cabc.Callable[[], None],
    ) -> object:
        """Invoke the cancel callback, then simulate a timeout."""
        del func, deadline
        call_count["value"] += 1
        cancel()
        raise TimeoutError("boom")

    monkeypatch.setattr(client, "_run_blocking_io", fake_run)

    with pytest.raises(TimeoutError):
        client._send_pipe_request(
            pathlib.Path("pipe"),
            b"{}",
            _ConnectionContext(timeout=0.1, retry_config=RetryConfig()),
        )

    assert call_count["value"] == 1, "Assertion failed"
    assert fake_handle.closed is True, "Assertion failed"


def test_run_blocking_io_times_out_and_invokes_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper should abort hung I/O and trigger the cancel callback."""
    cancel_calls: list[str] = []

    def long_running() -> None:
        """Outlive the join budget, then exit without needing cancellation."""
        time.sleep(0.15)

    def cancel() -> None:
        """Record the cancel call."""
        cancel_calls.append("called")

    monkeypatch.setattr(client, "_remaining_time", lambda _deadline: 0.0)

    with pytest.raises(TimeoutError):
        client._run_blocking_io(long_running, deadline=1.0, cancel=cancel)

    assert cancel_calls == ["called"], "Assertion failed"


class _RecordingCreateFile:
    """Fake ``win32file`` recording ``CreateFile`` arguments."""

    GENERIC_READ = 0x8000_0000
    GENERIC_WRITE = 0x4000_0000
    OPEN_EXISTING = 3

    def __init__(self, handle: object, error: BaseException | None = None) -> None:
        """Store the handle to return and an optional failure to raise."""
        self.handle = handle
        self.error = error
        self.calls: list[tuple[object, ...]] = []
        self.closes: list[object] = []

    def CreateFile(self, *args: object) -> object:  # ruff: ignore[invalid-function-name] - mirrors the Windows API casing
        """Record the call and return the handle or raise the configured error."""
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return self.handle

    def CloseHandle(self, handle: object) -> None:  # ruff: ignore[invalid-function-name] - mirrors the Windows API casing
        """Record each handle close."""
        self.closes += [handle]


class _RecordingPipeState:
    """Fake ``win32pipe`` recording named-pipe mode changes.

    The real module always defines ``PIPE_READMODE_MESSAGE``. Keeping it off
    this base class lets the fallback test prove the literal default is used
    when a pywin32 build omits the constant.
    """

    def __init__(self, error: BaseException | None = None) -> None:
        """Store an optional failure raised by ``SetNamedPipeHandleState``."""
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    def SetNamedPipeHandleState(self, *args: object) -> None:  # ruff: ignore[invalid-function-name] - mirrors the Windows API casing
        """Record the mode-setting call and optionally fail."""
        self.calls.append(args)
        if self.error is not None:
            raise self.error


class _MessageModePipeState(_RecordingPipeState):
    """Recording pipe state exposing a distinctive message-mode constant."""

    PIPE_READMODE_MESSAGE = 0x2D


def test_create_pipe_handle_uses_exact_win32_open_and_mode_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handle is opened read/write in message mode with fixed flags.

    A distinctive constant proves the module attribute is preferred over the
    literal fallback; the fallback itself is covered separately.
    """
    handle = object()
    win32file = _RecordingCreateFile(handle)
    win32pipe = _MessageModePipeState()
    monkeypatch.setattr(client, "win32file", win32file)
    monkeypatch.setattr(client, "win32pipe", win32pipe)

    result = client._create_pipe_handle(r"\\.\pipe\cmd-mox")

    assert result is handle, "the opened handle was not returned"
    expected_create = [
        (
            r"\\.\pipe\cmd-mox",
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
    ]
    assert win32file.calls == expected_create, (
        "CreateFile did not receive the exact Windows open arguments"
    )
    assert win32pipe.calls == [(handle, 0x2D, None, None)], (
        "the handle did not use the module's message-mode constant"
    )


def test_create_pipe_handle_defaults_message_mode_to_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pywin32 build without the constant still selects message mode."""
    handle = object()
    win32file = _RecordingCreateFile(handle)
    win32pipe = _RecordingPipeState()
    monkeypatch.setattr(client, "win32file", win32file)
    monkeypatch.setattr(client, "win32pipe", win32pipe)

    client._create_pipe_handle("pipe")

    assert win32pipe.calls == [(handle, 2, None, None)], (
        "the read-mode fallback was not the literal message mode"
    )


def test_create_pipe_handle_propagates_the_open_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ``CreateFile`` surfaces verbatim and skips mode setting."""
    error = _DummyPipeError(windows.ERROR_PIPE_BUSY)
    win32file = _RecordingCreateFile(object(), error=error)
    win32pipe = _RecordingPipeState()
    monkeypatch.setattr(client, "win32file", win32file)
    monkeypatch.setattr(client, "win32pipe", win32pipe)

    with pytest.raises(_DummyPipeError) as excinfo:
        client._create_pipe_handle("pipe")

    assert excinfo.value is error, "the pipe error was replaced"
    assert not win32pipe.calls, "mode setting ran without a handle"


def test_create_pipe_handle_propagates_the_mode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed mode change surfaces the original pywin32 error."""
    handle = object()
    error = _DummyPipeError(87)
    win32file = _RecordingCreateFile(handle)
    win32pipe = _RecordingPipeState(error=error)
    monkeypatch.setattr(client, "win32file", win32file)
    monkeypatch.setattr(client, "win32pipe", win32pipe)

    with pytest.raises(_DummyPipeError) as excinfo:
        client._create_pipe_handle("pipe")

    assert excinfo.value is error, "the mode-setting error was replaced"


class _PipeWaitClock:
    """Mutable monotonic clock for the pipe-availability tests."""

    def __init__(self, now: float = 0.0) -> None:
        """Start the clock at *now*."""
        self.now = now


def _bind_pipe_wait_clock(
    monkeypatch: pytest.MonkeyPatch, clock: _PipeWaitClock
) -> None:
    """Replace ``_remaining_time`` with one measured against *clock*."""

    def remaining(deadline: float) -> float:
        """Measure the budget against the mutable test clock."""
        left = deadline - clock.now
        if left <= 0:
            raise TimeoutError(_TIMEOUT_MESSAGE)
        return left

    monkeypatch.setattr(client, "_remaining_time", remaining)


@pytest.mark.parametrize(
    ("delay", "expected_ms"),
    [
        pytest.param(1.0, 1000, id="whole-second"),
        pytest.param(0.25, 250, id="fractional-second"),
        pytest.param(0.0019, 1, id="sub-millisecond-truncates"),
        pytest.param(0.0004, 1, id="floored-to-one-millisecond"),
    ],
)
def test_wait_for_pipe_availability_bounds_the_wait_in_milliseconds(
    monkeypatch: pytest.MonkeyPatch, delay: float, expected_ms: int
) -> None:
    """Waits convert seconds to milliseconds with a one-millisecond floor."""
    waits: list[tuple[str, int]] = []
    monkeypatch.setattr(
        client,
        "win32pipe",
        types.SimpleNamespace(
            WaitNamedPipe=lambda name, wait_ms: waits.append((name, wait_ms))
        ),
    )

    client._wait_for_pipe_availability("pipe", delay)

    assert waits == [("pipe", expected_ms)], "the wait duration was mis-converted"


def test_wait_for_pipe_availability_caps_the_wait_to_the_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A near deadline shortens the wait below the requested delay."""
    clock = _PipeWaitClock(now=10.0)
    waits: list[tuple[str, int]] = []
    _bind_pipe_wait_clock(monkeypatch, clock)
    monkeypatch.setattr(
        client,
        "win32pipe",
        types.SimpleNamespace(
            WaitNamedPipe=lambda name, wait_ms: waits.append((name, wait_ms))
        ),
    )

    client._wait_for_pipe_availability("pipe", 1.0, deadline=10.25)

    assert waits == [("pipe", 250)], "the wait overran the remaining deadline"


@pytest.mark.parametrize(
    ("now", "deadline"),
    [
        pytest.param(10.0, 9.75, id="expired"),
        pytest.param(10.25, 10.25, id="exactly-reached"),
    ],
)
def test_wait_for_pipe_availability_refuses_an_exhausted_deadline(
    monkeypatch: pytest.MonkeyPatch, now: float, deadline: float
) -> None:
    """An exhausted deadline raises without polling or sleeping."""
    clock = _PipeWaitClock(now=now)
    waits: list[object] = []
    sleeps: list[float] = []
    _bind_pipe_wait_clock(monkeypatch, clock)
    monkeypatch.setattr(
        client,
        "win32pipe",
        types.SimpleNamespace(WaitNamedPipe=lambda *_args: waits.append(_args)),
    )
    monkeypatch.setattr(client.time, "sleep", sleeps.append)

    with pytest.raises(TimeoutError):
        client._wait_for_pipe_availability("pipe", 1.0, deadline=deadline)

    assert not waits, "an exhausted deadline still polled the pipe"
    assert not sleeps, "an exhausted deadline still slept"


def test_wait_for_pipe_availability_sleeps_for_the_capped_wait_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pipe error falls back to sleeping for the bounded wait."""
    clock = _PipeWaitClock(now=10.0)
    sleeps: list[float] = []
    _bind_pipe_wait_clock(monkeypatch, clock)

    def wait(_name: str, _wait_ms: int) -> None:
        """Report the pipe as busy so the fallback sleep runs."""
        raise _DummyPipeError(windows.ERROR_PIPE_BUSY)

    monkeypatch.setattr(client, "win32pipe", types.SimpleNamespace(WaitNamedPipe=wait))
    monkeypatch.setattr(client.time, "sleep", sleeps.append)

    client._wait_for_pipe_availability("pipe", 1.0, deadline=10.25)

    assert sleeps == [0.25], "the fallback slept past the remaining deadline"


def test_wait_for_pipe_availability_propagates_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``pywintypes.error`` triggers the fallback sleep."""
    error = LookupError("unexpected")
    sleeps: list[float] = []

    def wait(_name: str, _wait_ms: int) -> None:
        """Raise an error type the fallback must not swallow."""
        raise error

    monkeypatch.setattr(client, "win32pipe", types.SimpleNamespace(WaitNamedPipe=wait))
    monkeypatch.setattr(client.time, "sleep", sleeps.append)

    with pytest.raises(LookupError) as excinfo:
        client._wait_for_pipe_availability("pipe", 0.5)

    assert excinfo.value is error, "the unexpected error was replaced"
    assert not sleeps, "an unexpected error still slept"


class _ScriptedPipeHandleFactory:
    """Scripted ``_create_pipe_handle`` recording every pipe-name argument."""

    def __init__(self, outcomes: list[object]) -> None:
        """Store the outcomes to return or raise in order."""
        self.outcomes = list(outcomes)
        self.calls: list[str] = []

    def __call__(self, pipe_name: str) -> object:
        """Record the pipe name and return or raise the next outcome."""
        self.calls.append(pipe_name)
        match self.outcomes.pop(0):
            case BaseException() as error:
                raise error
            case outcome:
                return outcome


def _bind_pipe_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[str, float, float | None]]:
    """Replace ``_wait_for_pipe_availability`` with a recorder."""
    waits: list[tuple[str, float, float | None]] = []

    def wait(pipe_name: str, delay: float, *, deadline: float | None = None) -> None:
        """Record the forwarded wait arguments."""
        waits.append((pipe_name, delay, deadline))

    monkeypatch.setattr(client, "_wait_for_pipe_availability", wait)
    return waits


@pytest.mark.parametrize(
    "winerror",
    [windows.ERROR_PIPE_BUSY, windows.ERROR_FILE_NOT_FOUND],
    ids=["busy", "missing"],
)
def test_connect_pipe_with_retries_retries_busy_and_missing_pipes(
    monkeypatch: pytest.MonkeyPatch, winerror: int
) -> None:
    """Busy and missing pipes are retried with the computed deadline."""
    error = _DummyPipeError(winerror)
    factory = _ScriptedPipeHandleFactory([error, "HANDLE"])
    waits = _bind_pipe_waits(monkeypatch)
    monkeypatch.setattr(client, "_create_pipe_handle", factory)
    monkeypatch.setattr(client, "_compute_deadline", lambda _timeout: 50.0)

    handle = client._connect_pipe_with_retries(
        "pipe",
        _ConnectionContext(
            timeout=1.0,
            retry_config=RetryConfig(retries=3, backoff=0.05, jitter=0.0),
        ),
    )

    assert handle == "HANDLE", "the retried connection did not return its handle"
    assert factory.calls == ["pipe", "pipe"], "the pipe name was not retried verbatim"
    assert waits == [("pipe", pytest.approx(0.05), 50.0)], (
        "the retry did not wait on the computed connect deadline"
    )


def test_connect_pipe_with_retries_converts_a_path_like_to_its_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``Path`` identifier reaches Win32 as the filesystem string."""
    factory = _ScriptedPipeHandleFactory(["HANDLE"])
    monkeypatch.setattr(client, "_create_pipe_handle", factory)
    pipe_path = pathlib.Path("nested") / "ipc.pipe"

    client._connect_pipe_with_retries(
        pipe_path,
        _ConnectionContext(
            timeout=1.0,
            retry_config=RetryConfig(retries=1, backoff=0.0, jitter=0.0),
        ),
    )

    assert factory.calls == [os.fspath(pipe_path)], (
        "the path-like identifier was not converted before opening"
    )


def test_connect_pipe_with_retries_honours_an_explicit_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit deadline is used without computing a new one."""
    error = _DummyPipeError(windows.ERROR_PIPE_BUSY)
    factory = _ScriptedPipeHandleFactory([error, "HANDLE"])
    waits = _bind_pipe_waits(monkeypatch)
    monkeypatch.setattr(client, "_create_pipe_handle", factory)
    monkeypatch.setattr(
        client,
        "_compute_deadline",
        lambda _timeout: pytest.fail("an explicit deadline was recomputed"),
    )

    client._connect_pipe_with_retries(
        "pipe",
        _ConnectionContext(
            timeout=1.0,
            retry_config=RetryConfig(retries=2, backoff=0.0, jitter=0.0),
        ),
        deadline=7.5,
    )

    assert waits == [("pipe", pytest.approx(0.001), 7.5)], (
        "the explicit connect deadline was not forwarded to the wait"
    )


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(_DummyPipeError(windows.ERROR_NO_DATA), id="non-retryable-code"),
        pytest.param(OSError("unexpected"), id="unrelated-error"),
    ],
)
def test_connect_pipe_with_retries_propagates_non_retryable_errors(
    monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    """Non-retryable failures surface immediately and are never waited on."""
    factory = _ScriptedPipeHandleFactory([error])
    waits = _bind_pipe_waits(monkeypatch)
    monkeypatch.setattr(client, "_create_pipe_handle", factory)

    with pytest.raises(type(error)) as excinfo:
        client._connect_pipe_with_retries(
            "pipe",
            _ConnectionContext(
                timeout=1.0,
                retry_config=RetryConfig(retries=3, backoff=0.05, jitter=0.0),
            ),
        )

    assert excinfo.value is error, "the non-retryable error was replaced"
    assert len(factory.calls) == 1, "a non-retryable error was retried"
    assert not waits, "a non-retryable error still waited"


def test_connect_pipe_with_retries_raises_the_last_busy_error_after_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exhaustion re-raises the final retryable error verbatim."""
    first = _DummyPipeError(windows.ERROR_PIPE_BUSY)
    last = _DummyPipeError(windows.ERROR_PIPE_BUSY)
    factory = _ScriptedPipeHandleFactory([first, last])
    waits = _bind_pipe_waits(monkeypatch)
    monkeypatch.setattr(client, "_create_pipe_handle", factory)

    with pytest.raises(_DummyPipeError) as excinfo:
        client._connect_pipe_with_retries(
            "pipe",
            _ConnectionContext(
                timeout=1.0,
                retry_config=RetryConfig(retries=2, backoff=0.0, jitter=0.0),
            ),
        )

    assert excinfo.value is last, "the final pipe error was not propagated"
    assert len(factory.calls) == 2, "the configured retry count was not respected"
    assert len(waits) == 1, "a wait ran after the final attempt"


def test_connect_pipe_with_retries_uses_linear_zero_jitter_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zero-jitter backoff grows linearly by the configured step."""
    busy = _DummyPipeError(windows.ERROR_PIPE_BUSY)
    factory = _ScriptedPipeHandleFactory([busy, busy, "HANDLE"])
    waits = _bind_pipe_waits(monkeypatch)
    monkeypatch.setattr(client, "_create_pipe_handle", factory)

    client._connect_pipe_with_retries(
        "pipe",
        _ConnectionContext(
            timeout=10.0,
            retry_config=RetryConfig(retries=3, backoff=0.05, jitter=0.0),
        ),
    )

    assert [delay for _name, delay, _deadline in waits] == pytest.approx([0.05, 0.1]), (
        "the backoff was not linear from the configured step"
    )


def test_connect_pipe_with_retries_emits_named_pipe_retry_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every failed named-pipe attempt emits a bounded retry event."""
    from cmd_mox.ipc import _observability, client

    busy = _DummyPipeError(windows.ERROR_PIPE_BUSY)
    factory = _ScriptedPipeHandleFactory([busy, "HANDLE"])
    _bind_pipe_waits(monkeypatch)
    monkeypatch.setattr(client, "_create_pipe_handle", factory)

    with _observability.capture_events() as events:
        client._connect_pipe_with_retries(
            "pipe",
            _ConnectionContext(
                timeout=1.0,
                retry_config=RetryConfig(retries=2, backoff=0.0, jitter=0.0),
            ),
        )

    retries = [
        event for event in events if event.operation == "ipc.client.connect_retry"
    ]
    assert len(retries) == 1, "one retry event was expected"
    assert retries[0].transport == "named_pipe", "the transport was mislabelled"
    assert retries[0].attempt == 1, "retry attempts must be reported 1-based"
    assert retries[0].error_category == "_DummyPipeError", "the category was lost"


def test_pipe_handle_closer_suppresses_pywintypes_errors_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The close guard ignores pywin32 failures and repeat calls."""
    handle = object()
    closes: list[object] = []

    def close_handle(_handle: object) -> None:
        """Record the close, then fail the way pywin32 may."""
        closes.append(_handle)
        raise _DummyPipeError(6)

    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(CloseHandle=close_handle),
    )

    closer = client._pipe_handle_closer(handle)
    closer.close()
    closer.close()

    assert closes == [handle], "the handle close was not attempted exactly once"
