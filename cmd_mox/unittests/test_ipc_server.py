"""Unit tests for the IPC server component."""

import json
import logging
import os
import socket
import threading
import time
import types
import typing as typ
from pathlib import Path

import pytest

from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    EnvironmentManager,
)
from cmd_mox.ipc import (
    DEFAULT_CONNECT_JITTER,
    Invocation,
    IPCServer,
    RetryConfig,
    _deadline,
    _observability,
    invoke_server,
)
from cmd_mox.ipc import client as ipc_client_module
from cmd_mox.ipc import server as ipc_server_module
from cmd_mox.ipc.server import _IPCHandler

pytestmark = pytest.mark.requires_unix_sockets


def _is_readiness_close_event(event: _observability.IPCEvent) -> bool:
    """Return whether an event represents a closed readiness probe.

    Returns
    -------
    bool
        Whether *event* records a readiness probe that closed without a request.
    """
    if event.operation != "ipc.server.connection":
        return False
    if event.outcome != "receive_closed":
        return False
    return event.error_category == "ConnectionClosed"


def _wait_for_readiness_close_event(
    events: list[_observability.IPCEvent], timeout: float = 1.0
) -> _observability.IPCEvent:
    """Wait for the connection event emitted by a readiness probe.

    Parameters
    ----------
    events:
        Events captured while the IPC server is running.
    timeout:
        Maximum time to wait for the readiness connection event.

    Returns
    -------
    _observability.IPCEvent
        The event emitted when the readiness probe closes.

    Raises
    ------
    AssertionError
        If no matching event is captured before *timeout* expires.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        event = next((item for item in events if _is_readiness_close_event(item)), None)
        if event is not None:
            return event
        time.sleep(0.01)
    raise AssertionError


def test_ipc_server_start_stop(tmp_path: Path) -> None:
    """Server creates and removes the socket path."""
    socket_path = tmp_path / "ipc.sock"
    server = IPCServer(socket_path)
    server.start()
    assert socket_path.exists()
    server.stop()
    assert not socket_path.exists()


def test_ipc_server_restart(tmp_path: Path) -> None:
    """Server instance can be started again after stopping."""
    socket_path = tmp_path / "ipc.sock"
    server = IPCServer(socket_path)
    server.start()
    server.stop()
    server.start()
    assert socket_path.exists()
    server.stop()


def test_ipc_server_exports_environment() -> None:
    """Starting IPCServer under EnvironmentManager publishes env vars."""
    original_socket = os.environ.get(CMOX_IPC_SOCKET_ENV)
    original_timeout = os.environ.get(CMOX_IPC_TIMEOUT_ENV)

    with EnvironmentManager() as env:
        assert env.socket_path is not None
        with IPCServer(env.socket_path, timeout=1.25):
            assert os.environ[CMOX_IPC_SOCKET_ENV] == str(env.socket_path)
            assert os.environ[CMOX_IPC_TIMEOUT_ENV] == "1.25"

    assert os.environ.get(CMOX_IPC_SOCKET_ENV) == original_socket
    assert os.environ.get(CMOX_IPC_TIMEOUT_ENV) == original_timeout


def test_ipc_server_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Server echoes command name via IPC."""
    socket_path = tmp_path / "ipc.sock"
    with IPCServer(socket_path):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="ls", args=["-l"], stdin="", env={})
        response = invoke_server(invocation, timeout=2.0)
        assert response.stdout == "ls"


def test_ipc_server_readiness_probe_is_a_closed_connection(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Readiness probes close cleanly without being logged as malformed JSON."""
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc._server_core")

    with _observability.capture_events() as events, IPCServer(tmp_path / "ipc.sock"):
        event = _wait_for_readiness_close_event(events)

    assert "connection closed" in caplog.text, "Readiness probe closure was not named"
    assert "malformed JSON" not in caplog.text, "Readiness probe was called malformed"
    assert event.correlation_id is None, "Readiness probe must not carry correlation"


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


def test_ipc_handler_uses_remaining_time_for_each_receive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slow request chunks cannot renew the connection's read budget."""
    now = {"value": 5.0}
    monkeypatch.setattr(_deadline.time, "monotonic", lambda: now["value"])

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
        "_IPCHandler",
        types.SimpleNamespace(request=request, _read_deadline=6.0),
    )

    payload = _IPCHandler._read_request_payload(fake_handler)

    assert payload == b"firstsecond"
    assert request.timeouts == pytest.approx([1.0, 0.75, 0.5])


@pytest.mark.parametrize(
    "failure_case",
    [
        ("write", BrokenPipeError, "ConnectionError"),
        ("flush", ConnectionResetError, "ConnectionError"),
        ("flush", OSError, "OSError"),
    ],
)
def test_ipc_handler_logs_response_connection_drop(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    failure_case: tuple[str, type[OSError], str],
) -> None:
    """Response write and flush failures become bounded connection-drop logs."""
    stage, failure_type, expected_category = failure_case

    class BrokenWriter:
        def write(self, _text: str) -> int:
            """Fail only when the parametrized stage is response writing.

            Returns
            -------
            int
                One accepted byte when the test targets response flushing.
            """
            if stage == "write":
                raise failure_type
            return 1

        def flush(self) -> None:
            """Fail only when the parametrized stage is response flushing."""
            if stage == "flush":
                raise failure_type

    fake_request = types.SimpleNamespace(
        settimeout=lambda _timeout: None,
    )
    fake_handler = typ.cast(
        "_IPCHandler",
        types.SimpleNamespace(
            request=fake_request,
            _read_request_payload=lambda: b"request",
            _read_deadline=time.monotonic() + 1.0,
            server=types.SimpleNamespace(outer=types.SimpleNamespace(timeout=1.0)),
            wfile=BrokenWriter(),
        ),
    )
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc.server")
    monkeypatch.setattr(
        ipc_server_module,
        "_request_pipeline",
        lambda *_args: types.SimpleNamespace(
            response_bytes=b"reply", correlation_id=None
        ),
    )

    with _observability.capture_events() as events:
        _IPCHandler.handle(fake_handler)

    assert "connection closed" in caplog.text, (
        "Dropped response connection was not logged"
    )
    drops = [
        event
        for event in events
        if event.operation == "ipc.server.connection"
        and event.outcome == "response_dropped"
    ]
    assert len(drops) == 1, "Dropped response must emit exactly one connection event"
    assert drops[0].error_category == expected_category, (
        "Dropped response reported the wrong bounded error category"
    )


def test_ipc_handler_records_receive_connection_drop() -> None:
    """A reset while reading emits a bounded closed-connection event."""

    def fail_read() -> bytes:
        """Simulate a reset before the request can be received.

        Raises
        ------
        ConnectionResetError
            Always, before any request bytes are received.
        """
        raise ConnectionResetError

    fake_handler = typ.cast(
        "_IPCHandler",
        types.SimpleNamespace(
            _read_request_payload=fail_read,
            server=types.SimpleNamespace(outer=types.SimpleNamespace(timeout=1.0)),
        ),
    )

    with _observability.capture_events() as events:
        _IPCHandler.handle(fake_handler)

    assert any(
        event.operation == "ipc.server.connection"
        and event.outcome == "receive_closed"
        and event.error_category == "ConnectionError"
        for event in events
    ), "Receive-side connection reset did not emit a bounded event"


def _assert_response_drop_is_correlated(
    events: list[_observability.IPCEvent],
    caplog: pytest.LogCaptureFixture,
    correlation_id: str,
    count_before: int,
) -> None:
    """Check drop diagnostics retain only bounded request metadata."""
    [event] = [
        event
        for event in events
        if event.operation == "ipc.server.connection"
        and event.outcome == "response_dropped"
    ]
    assert event.transport == "unix", "Connection event omitted its transport"
    assert event.error_category == "ConnectionError", "Wrong bounded error category"
    assert event.correlation_id == correlation_id, (
        "Response drop lost request correlation"
    )
    assert event.duration_ms is not None, "Connection event omitted its duration"
    assert event.duration_ms >= 0, "Connection event duration must be nonnegative"
    counter_key = _observability.EventKey(
        "ipc.server.connection", "unix", "response_dropped"
    )
    assert _observability.counter_snapshot()[counter_key] == count_before + 1, (
        "Response drop must increment its bounded counter once"
    )
    assert correlation_id not in counter_key, (
        "Correlation id must not affect counter keys"
    )

    [record] = [
        record
        for record in caplog.records
        if getattr(record, "operation", None) == "ipc.server.connection"
        and getattr(record, "outcome", None) == "response_dropped"
    ]
    assert getattr(record, "correlation_id", None) == correlation_id, (
        "Structured log lost request correlation"
    )
    assert "secret-command" not in caplog.text, "Request payload leaked into logs"
    assert "secret-stdin" not in caplog.text, "Request stdin leaked into logs"
    assert "secret-exception-detail" not in caplog.text, (
        "Exception detail leaked into logs"
    )


def test_ipc_handler_correlates_response_drop_with_validated_envelope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dropped response keeps its bounded request correlation identifier."""
    correlation_id = "request-123"
    raw = json.dumps({
        "kind": "invocation",
        "correlation_id": correlation_id,
        "command": "secret-command",
        "args": [],
        "stdin": "secret-stdin",
        "env": {},
    }).encode()

    class BrokenWriter:
        flush_called = False

        def write(self, _payload: bytes) -> int:
            """Fail the response write to exercise correlated drop logging.

            Raises
            ------
            BrokenPipeError
                Always, simulating a client that closed before its response.
            """
            raise BrokenPipeError("secret-exception-detail")

        def flush(self) -> None:
            """Record whether the handler flushes after a failed write."""
            self.flush_called = True

    writer = BrokenWriter()
    fake_handler = typ.cast(
        "_IPCHandler",
        types.SimpleNamespace(
            request=types.SimpleNamespace(settimeout=lambda _timeout: None),
            _read_request_payload=lambda: raw,
            _read_deadline=time.monotonic() + 1.0,
            server=types.SimpleNamespace(outer=types.SimpleNamespace(timeout=1.0)),
            wfile=writer,
        ),
    )
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc.server")
    counter_key = _observability.EventKey(
        "ipc.server.connection", "unix", "response_dropped"
    )
    count_before = _observability.counter_snapshot().get(counter_key, 0)

    with _observability.capture_events() as events:
        _IPCHandler.handle(fake_handler)

    _assert_response_drop_is_correlated(
        events,
        caplog,
        correlation_id,
        count_before,
    )
    assert not writer.flush_called, "Flush ran after a failed write"


def test_ipc_server_start_fails_if_in_use(tmp_path: Path) -> None:
    """Starting a second server on the same socket raises RuntimeError."""
    socket_path = tmp_path / "ipc.sock"
    with IPCServer(socket_path):
        other = IPCServer(socket_path)
        with pytest.raises(RuntimeError, match="in use"):
            other.start()


@pytest.mark.parametrize(
    ("jitter", "_description"),
    [
        (None, "default jitter"),
        (0.5, "custom jitter=0.5"),
    ],
)
def test_invoke_server_retries_connection_parametrized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    jitter: float | None,
    _description: str,
) -> None:
    """Client retries until the server becomes available."""
    socket_path = tmp_path / "ipc.sock"

    server = IPCServer(socket_path)
    emit_connect_retry = ipc_client_module._client_events.emit_connect_retry
    server_started = False

    def start_server_after_first_failure(
        transport: _observability.Transport,
        attempt: int,
        exception: Exception,
        correlation_id: str | None,
    ) -> None:
        nonlocal server_started
        emit_connect_retry(transport, attempt, exception, correlation_id)
        if not server_started:
            server_started = True
            server.start()

    monkeypatch.setattr(
        ipc_client_module._client_events,
        "emit_connect_retry",
        start_server_after_first_failure,
    )
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="ls", args=[], stdin="", env={})
    try:
        if jitter is None:
            retry_config = RetryConfig(retries=5, backoff=0.01)
        else:
            retry_config = RetryConfig(retries=5, backoff=0.01, jitter=jitter)
        response = invoke_server(invocation, timeout=1.0, retry_config=retry_config)
        assert response.stdout == "ls"
    finally:
        server.stop()


def test_invoke_server_exhausts_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client gives up after exceeding the retry limit."""
    socket_path = tmp_path / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="ls", args=[], stdin="", env={})
    with pytest.raises(FileNotFoundError):
        invoke_server(
            invocation,
            timeout=0.1,
            retry_config=RetryConfig(retries=1, backoff=0.01),
        )


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"retries": 0}, "retries must"),
        ({"timeout": 0.0}, "timeout must"),
        ({"timeout": float("inf")}, "timeout must"),
        ({"backoff": -0.1}, "backoff must"),
        ({"backoff": float("nan")}, "backoff must"),
        ({"jitter": -0.1}, "jitter must"),
        ({"jitter": 1.1}, "jitter must"),
        ({"jitter": float("nan")}, "jitter must"),
    ],
)
def test_invoke_server_validates_params(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kwargs: dict[str, typ.Any],
    match: str,
) -> None:
    """Client rejects invalid retry configuration."""
    socket_path = tmp_path / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="ls", args=[], stdin="", env={})
    timeout = kwargs.get("timeout", 1.0)
    retries = kwargs.get("retries", 1)
    backoff = kwargs.get("backoff", 0.0)
    jitter = kwargs.get("jitter", DEFAULT_CONNECT_JITTER)
    with pytest.raises(ValueError, match=match):
        invoke_server(
            invocation,
            timeout=timeout,
            retry_config=RetryConfig(
                retries=retries,
                backoff=backoff,
                jitter=jitter,
            ),
        )


def test_invoke_server_invalid_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client raises RuntimeError on malformed JSON from server."""
    socket_path = tmp_path / "ipc.sock"
    srv_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv_sock.bind(str(socket_path))
    srv_sock.listen(1)

    def serve() -> None:
        conn, _ = srv_sock.accept()
        conn.recv(1024)
        conn.sendall(b"not-json")
        conn.close()
        srv_sock.close()

    thread = threading.Thread(target=serve)
    thread.start()
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="ls", args=[], stdin="", env={})
    with pytest.raises(RuntimeError, match="Invalid JSON"):
        invoke_server(invocation, timeout=1.0)
    thread.join()
