"""Regression tests for IPC server request-handler setup."""

import collections.abc as cabc
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


def test_ipc_server_reports_timeout_when_deadline_expires_before_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired setup deadline is reported by the handler's read path."""
    socket_path = tmp_path / "ipc.sock"

    with _observability.capture_events() as events, IPCServer(socket_path):
        monkeypatch.setattr(
            ipc_server_module,
            "compute_deadline",
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


def test_ipc_handler_uses_the_remaining_budget_for_each_receive() -> None:
    """Slow request chunks cannot renew the connection's read budget."""
    now = {"value": 5.0}

    def clock() -> float:
        """Return the fake clock's current time."""
        return now["value"]

    class SlowRequest:
        """Fake request socket that advances the clock on each receive."""

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
        types.SimpleNamespace(request=request, _connection_deadline=6.0, _clock=clock),
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


class _ReplyWriter:
    """Record the reply steps that reach the socket."""

    def __init__(self, clock: cabc.Callable[[float], None] | None = None) -> None:
        """Optionally run *clock* on each write to spend reply budget."""
        self._advance = clock
        self.writes: list[bytes] = []
        self.flush_called = False

    def write(self, payload: bytes) -> int:
        """Record the payload, spending budget when the test asked for it.

        Returns
        -------
        int
            Number of payload bytes accepted.
        """
        self.writes.append(payload)
        if self._advance is not None:
            self._advance(2.0)
        return len(payload)

    def flush(self) -> None:
        """Record the flush."""
        self.flush_called = True


def _reply_handler(
    writer: _ReplyWriter,
    clock: cabc.Callable[[], float],
    *,
    deadline: float,
    timeouts: list[float],
) -> types.SimpleNamespace:
    """Build the smallest handler double that reaches the reply path.

    Returns
    -------
    SimpleNamespace
        A handler double sharing *writer*, *clock* and *deadline*.
    """
    return types.SimpleNamespace(
        request=types.SimpleNamespace(settimeout=timeouts.append),
        _read_request_payload=lambda: b"request",
        _connection_deadline=deadline,
        _clock=clock,
        wfile=writer,
        server=types.SimpleNamespace(outer=types.SimpleNamespace(timeout=1.0)),
    )


def test_ipc_handler_redraws_the_connection_budget_before_each_reply_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reply write and flush each use what is left of one budget."""
    now = {"value": 0.0}

    def clock() -> float:
        """Return the fake clock's current time."""
        return now["value"]

    def spend(seconds: float) -> None:
        """Advance the fake clock by *seconds*."""
        now["value"] += seconds

    monkeypatch.setattr(
        _ipc_server,
        "_request_pipeline",
        lambda _server, _raw, _transport: types.SimpleNamespace(
            response_bytes=b"reply", correlation_id="request-123"
        ),
    )
    writer = _ReplyWriter(spend)
    timeouts: list[float] = []
    handler = _reply_handler(writer, clock, deadline=5.0, timeouts=timeouts)

    _ipc_server._IPCHandler.handle(typ.cast("_ipc_server._IPCHandler", handler))

    assert timeouts == pytest.approx([5.0, 3.0]), (
        "The flush did not re-draw the remaining connection budget"
    )
    assert writer.writes == [b"reply"], "The reply was not written"
    assert writer.flush_called, "The reply was not flushed"


def test_ipc_handler_drops_the_reply_when_the_connection_budget_is_spent(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A spent budget drops the reply instead of sending a second one."""
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc.server")
    monkeypatch.setattr(
        _ipc_server,
        "_request_pipeline",
        lambda _server, _raw, _transport: types.SimpleNamespace(
            response_bytes=b"reply", correlation_id="request-123"
        ),
    )
    writer = _ReplyWriter()
    timeouts: list[float] = []
    handler = _reply_handler(writer, lambda: 10.0, deadline=10.0, timeouts=timeouts)

    with _observability.capture_events() as events:
        _ipc_server._IPCHandler.handle(typ.cast("_ipc_server._IPCHandler", handler))

    assert not timeouts, "An expired budget still applied a socket timeout"
    assert not writer.writes, "An expired budget still attempted the reply"
    assert not writer.flush_called, "An expired budget still flushed"
    [event] = [
        item
        for item in events
        if item.operation == "ipc.server.connection"
        and item.outcome == "response_dropped"
    ]
    assert event.error_category == "TimeoutError", (
        "A spent reply budget reported the wrong bounded category"
    )
    assert event.correlation_id == "request-123", "Response drop lost correlation"
    assert "timed out while replying" in caplog.text, (
        "A spent reply budget was not reported at the reply diagnostic level"
    )
