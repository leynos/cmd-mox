"""Regression tests for IPC server request-handler setup."""

import logging
import socket
import time
import types
import typing as typ
from pathlib import Path

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import Invocation, _observability, invoke_server
from cmd_mox.ipc import server as _ipc_server
from cmd_mox.ipc import server as ipc_server_module
from cmd_mox.ipc.server import IPCServer

pytestmark = pytest.mark.requires_unix_sockets


def test_compute_deadline_uses_injected_clock() -> None:
    """Deadline construction uses the supplied monotonic clock."""
    now = 5.0

    deadline = ipc_server_module._compute_deadline(2.5, clock=lambda: now)

    assert deadline == pytest.approx(7.5), "Deadline did not use the injected clock"


def test_ipc_server_reports_timeout_when_deadline_expires_before_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired setup deadline is reported by the handler's read path."""
    socket_path = tmp_path / "ipc.sock"

    with _observability.capture_events() as events, IPCServer(socket_path):
        monkeypatch.setattr(
            ipc_server_module,
            "_compute_deadline",
            lambda _timeout, *, clock: clock() - 1.0,
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as incomplete:
            incomplete.settimeout(1.0)
            incomplete.connect(str(socket_path))
            deadline = time.monotonic() + 1.0
            timeout_event = None
            while time.monotonic() < deadline:
                timeout_event = next(
                    (
                        event
                        for event in events
                        if (event.operation, event.outcome)
                        == ("ipc.server.connection", "receive_timeout")
                    ),
                    None,
                )
                if timeout_event is not None:
                    break
                time.sleep(0.01)

    assert timeout_event is not None, (
        "Expired deadline did not emit a receive timeout event"
    )
    assert timeout_event.error_category == "TimeoutError", (
        "Expired receive deadline reported the wrong error category"
    )


def test_ipc_handler_uses_remaining_time_for_each_receive() -> None:
    """Slow request chunks cannot renew the connection's read budget."""
    now = {"value": 5.0}

    def clock() -> float:
        return now["value"]

    class SlowRequest:
        def __init__(self) -> None:
            """Queue fragments and collect the handler's timeout updates."""
            self.chunks = iter([b"first", b"second", b""])
            self.timeouts: list[float] = []

        def settimeout(self, timeout: float) -> None:
            """Record each remaining timeout applied before a receive."""
            self.timeouts.append(timeout)

        def recv(self, _size: int) -> bytes:
            """Advance fake time while returning the next request fragment.

            Returns
            -------
            bytes
                The next queued request fragment.
            """
            now["value"] += 0.25
            return next(self.chunks)

    request = SlowRequest()
    fake_handler = typ.cast(
        "_ipc_server._IPCHandler",
        types.SimpleNamespace(request=request, _read_deadline=6.0, _clock=clock),
    )

    payload = _ipc_server._IPCHandler._read_request_payload(fake_handler)

    assert payload == b"firstsecond", "Request payload fragments were not assembled"
    assert request.timeouts == pytest.approx([1.0, 0.75, 0.5]), (
        "Each receive should use the remaining request deadline"
    )


def test_ipc_server_bounds_incomplete_request_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A client that sends nothing times out and does not block later requests."""
    socket_path = tmp_path / "ipc.sock"
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc.server")

    with _observability.capture_events() as events, IPCServer(socket_path, timeout=0.1):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as incomplete:
            incomplete.settimeout(1.0)
            incomplete.connect(str(socket_path))
            deadline = time.monotonic() + 1.0
            while (
                "timed out while receiving request" not in caplog.text
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)

        assert "timed out while receiving request" in caplog.text, (
            "Incomplete request did not hit its per-connection read timeout"
        )
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        response = invoke_server(
            Invocation(command="after-timeout", args=[], stdin="", env={}),
            timeout=1.0,
        )

    assert response.stdout == "after-timeout", "Timed-out client stalled the server"
    [event] = [
        event
        for event in events
        if event.operation == "ipc.server.connection"
        and event.outcome == "receive_timeout"
        and event.error_category == "TimeoutError"
    ]
    assert event.correlation_id is None, "Incomplete request must not carry correlation"
