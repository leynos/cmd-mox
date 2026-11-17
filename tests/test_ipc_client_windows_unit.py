"""Unit tests for the Windows IPC client's named-pipe connection retries."""

from __future__ import annotations

import json
import os
import pathlib
import types

import pytest

from cmd_mox.ipc import _observability, client, windows
from cmd_mox.ipc.client import RetryConfig, _ConnectionContext
from cmd_mox.ipc.constants import KIND_INVOCATION
from cmd_mox.ipc.wire import WireFormatError
from tests.helpers.ipc_windows import DummyPipeError, patch_windows_modules


@pytest.fixture(autouse=True)
def _patch_windows_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub Windows-only modules so the named-pipe client runs on any platform."""
    patch_windows_modules(monkeypatch)


def test_connect_pipe_with_retries_eventually_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure named pipe connections retry when the backend is busy."""
    attempts = {"count": 0}

    def fake_create(_pipe_name: pathlib.Path, *_args: object) -> str:
        """Report a busy pipe on the first attempt, then return a handle."""
        attempts["count"] += 1
        if attempts["count"] < 2:
            raise DummyPipeError(windows.ERROR_PIPE_BUSY)
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
        raise DummyPipeError(windows.ERROR_NO_DATA)

    monkeypatch.setattr(client, "_create_pipe_handle", fake_create)

    with pytest.raises(DummyPipeError):
        client._connect_pipe_with_retries(
            pathlib.Path("pipe"),
            _ConnectionContext(
                timeout=0.1,
                retry_config=RetryConfig(retries=2, backoff=0.0, jitter=0.0),
            ),
        )


@pytest.mark.parametrize(
    ("reply", "error_type", "match"),
    [
        pytest.param(
            json.dumps({
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
                "env": {},
                "passthrough": {"invocation_id": "abc"},
            }).encode(),
            WireFormatError,
            "passthrough is missing",
            id="wrong-typed-passthrough",
        ),
        pytest.param(
            b'{"stdout": "", "passthrough": {"invocation_id": "a"}, '
            b'"passthrough": null}',
            RuntimeError,
            "Invalid JSON from IPC server",
            id="duplicate-key",
        ),
        pytest.param(
            b'{"exit_code": ' + b"9" * 5_000 + b"}",
            RuntimeError,
            "Invalid JSON from IPC server",
            id="oversized-integer",
        ),
        pytest.param(
            b"[" * 100_000 + b"]" * 100_000,
            RuntimeError,
            "Invalid JSON from IPC server",
            id="excessive-nesting",
        ),
    ],
)
def test_unexpected_pipe_reply_raises_bounded_client_error(
    monkeypatch: pytest.MonkeyPatch,
    reply: bytes,
    error_type: type[Exception],
    match: str,
) -> None:
    """A named-pipe reply that cannot be decoded surfaces as a bounded error."""
    from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
    from cmd_mox.ipc import client

    fake_handle = types.SimpleNamespace()
    monkeypatch.setattr(
        client,
        "_connect_pipe_with_retries",
        lambda *_args, **_kwargs: fake_handle,
    )
    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(
            WriteFile=lambda *_args, **_kwargs: None,
            FlushFileBuffers=lambda *_args, **_kwargs: None,
            CloseHandle=lambda _handle: None,
            ReadFile=lambda *_args, **_kwargs: (0, reply),
        ),
    )
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, "pipe")

    with pytest.raises(error_type, match=match):
        client._perform_request(
            b"{}",
            KIND_INVOCATION,
            # The worker touches only stand-ins, so a generous budget removes
            # the chance that thread startup on a loaded host consumes it.
            _ConnectionContext(timeout=5.0, retry_config=RetryConfig()),
        )


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
    error = DummyPipeError(winerror)
    factory = _ScriptedPipeHandleFactory([error, "HANDLE"])
    waits = _bind_pipe_waits(monkeypatch)
    monkeypatch.setattr(client, "_create_pipe_handle", factory)
    monkeypatch.setattr(client, "compute_deadline", lambda _timeout: 50.0)

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
    error = DummyPipeError(windows.ERROR_PIPE_BUSY)
    factory = _ScriptedPipeHandleFactory([error, "HANDLE"])
    waits = _bind_pipe_waits(monkeypatch)
    monkeypatch.setattr(client, "_create_pipe_handle", factory)
    monkeypatch.setattr(
        client,
        "compute_deadline",
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
        pytest.param(DummyPipeError(windows.ERROR_NO_DATA), id="non-retryable-code"),
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
    first = DummyPipeError(windows.ERROR_PIPE_BUSY)
    last = DummyPipeError(windows.ERROR_PIPE_BUSY)
    factory = _ScriptedPipeHandleFactory([first, last])
    waits = _bind_pipe_waits(monkeypatch)
    monkeypatch.setattr(client, "_create_pipe_handle", factory)

    with pytest.raises(DummyPipeError) as excinfo:
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
    busy = DummyPipeError(windows.ERROR_PIPE_BUSY)
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
    busy = DummyPipeError(windows.ERROR_PIPE_BUSY)
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
    assert retries[0].error_category == "DummyPipeError", "the category was lost"
