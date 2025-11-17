"""Unit tests for Windows-specific IPC client helpers."""

from __future__ import annotations

import pathlib
import threading
import types
import typing as t

import pytest

from cmd_mox.ipc import windows
from cmd_mox.ipc.client import RetryConfig


@pytest.fixture(autouse=True)
def _patch_windows_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    import cmd_mox.ipc.client as client

    monkeypatch.setattr(client, "IS_WINDOWS", True)
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
    def __init__(self, winerror: int) -> None:
        super().__init__(winerror)
        self.winerror = winerror


def test_connect_pipe_with_retries_eventually_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure named pipe connections retry when the backend is busy."""
    import cmd_mox.ipc.client as client

    attempts = {"count": 0}

    def fake_create(_pipe_name: pathlib.Path, *_args: object) -> str:
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
        timeout=0.1,
        retry_config=RetryConfig(retries=3, backoff=0.0, jitter=0.0),
    )

    assert handle == "HANDLE"
    assert attempts["count"] == 2


def test_connect_pipe_with_retries_raises_after_non_retryable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify non-retryable errors bubble up immediately."""
    import cmd_mox.ipc.client as client

    def fake_create(_pipe_name: pathlib.Path, *_args: object) -> str:
        raise _DummyPipeError(windows.ERROR_NO_DATA)

    monkeypatch.setattr(client, "_create_pipe_handle", fake_create)

    with pytest.raises(_DummyPipeError):
        client._connect_pipe_with_retries(
            pathlib.Path("pipe"),
            timeout=0.1,
            retry_config=RetryConfig(retries=2, backoff=0.0, jitter=0.0),
        )


def test_send_pipe_request_writes_and_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """``_send_pipe_request`` should write payloads and read responses."""
    import cmd_mox.ipc.client as client

    handle_log: list[str] = []

    class _FakeHandle:
        def __init__(self) -> None:
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
        timeout=0.1,
        retry_config=RetryConfig(),
    )

    assert response == b"{}"
    assert handle_log == ["{}"]
    assert fake_handle.closed is True


def test_send_pipe_request_closes_handle_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_send_pipe_request`` should close handles when timeouts surface."""
    import cmd_mox.ipc.client as client

    class _FakeHandle:
        def __init__(self) -> None:
            self.closed = False

    fake_handle = _FakeHandle()

    def fake_close(handle: _FakeHandle) -> None:
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
        func: t.Callable[[], object], *, deadline: float, cancel: t.Callable[[], None]
    ) -> object:
        del func, deadline
        call_count["value"] += 1
        cancel()
        raise TimeoutError("boom")

    monkeypatch.setattr(client, "_run_blocking_io", fake_run)

    with pytest.raises(TimeoutError):
        client._send_pipe_request(
            pathlib.Path("pipe"),
            b"{}",
            timeout=0.1,
            retry_config=RetryConfig(),
        )

    assert call_count["value"] == 1
    assert fake_handle.closed is True


def test_run_blocking_io_times_out_and_invokes_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper should abort hung I/O and trigger the cancel callback."""
    import cmd_mox.ipc.client as client

    stop_event = threading.Event()

    def long_running() -> None:
        stop_event.wait()

    cancel_calls: list[str] = []

    def cancel() -> None:
        cancel_calls.append("called")
        stop_event.set()

    monkeypatch.setattr(client, "_remaining_time", lambda _deadline: 0.0)

    with pytest.raises(TimeoutError):
        client._run_blocking_io(long_running, deadline=1.0, cancel=cancel)

    assert cancel_calls == ["called"]
