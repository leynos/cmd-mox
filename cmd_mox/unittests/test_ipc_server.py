"""Unit tests for the IPC server component."""

import contextlib
import errno
import io
import json
import logging
import os
import socket
import socketserver
import threading
import time
import types
import typing as typ
from pathlib import Path
from types import SimpleNamespace

import pytest

from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    EnvironmentManager,
)
from cmd_mox.ipc import (
    DEFAULT_CONNECT_JITTER,
    Invocation,
    IPCHandlers,
    IPCServer,
    Response,
    RetryConfig,
    _client_events,
    _deadline,
    _observability,
    _unix_server_base,
    invoke_server,
)
from cmd_mox.ipc import server as _ipc_server

pytestmark = pytest.mark.requires_unix_sockets


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


def test_ipc_server_resolves_unix_base_on_first_backend_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Resolve the Unix base lazily and preserve the public IPC roundtrip."""
    original_resolver = _unix_server_base._resolve_unix_server_base
    resolver_calls = 0

    def count_resolution() -> type[socketserver.BaseServer]:
        nonlocal resolver_calls
        resolver_calls += 1
        return original_resolver()

    monkeypatch.setattr(
        _unix_server_base,
        "_resolve_unix_server_base",
        count_resolution,
    )
    socket_path = tmp_path / "ipc-lazy.sock"
    server = IPCServer(socket_path)
    assert resolver_calls == 0, "Creating IPCServer must not resolve its base."

    with server:
        assert resolver_calls == 1, "Backend start must resolve its base once."
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        response = invoke_server(
            Invocation(command="ls", args=["-l"], stdin="", env={}),
            timeout=2.0,
        )
        assert response.stdout == "ls", "Server must echo the command name"

    assert resolver_calls == 1, "The successful base resolution must be cached."


def test_ipc_server_readiness_probe_is_a_closed_connection(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Readiness probes close cleanly without being logged as malformed JSON."""
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc._server_core")

    with _observability.capture_events() as events, IPCServer(tmp_path / "ipc.sock"):
        deadline = time.monotonic() + 1.0
        while (
            not any(
                event.operation == "ipc.server.connection"
                and event.outcome == "receive_closed"
                and event.error_category == "ConnectionClosed"
                for event in events
            )
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)

    assert "connection closed" in caplog.text, "Readiness probe closure was not named"
    assert "malformed JSON" not in caplog.text, "Readiness probe was called malformed"
    assert any(
        event.operation == "ipc.server.connection"
        and event.outcome == "receive_closed"
        and event.error_category == "ConnectionClosed"
        for event in events
    ), "Readiness probe did not emit its connection event"
    [event] = [
        event
        for event in events
        if event.operation == "ipc.server.connection"
        and event.outcome == "receive_closed"
        and event.error_category == "ConnectionClosed"
    ]
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
        "_ipc_server._IPCHandler",
        types.SimpleNamespace(request=request, _read_deadline=6.0),
    )

    payload = _ipc_server._IPCHandler._read_request_payload(fake_handler)

    assert payload == b"firstsecond"
    assert request.timeouts == pytest.approx([1.0, 0.75, 0.5])


@pytest.mark.parametrize(
    ("stage", "failure_type", "error_number", "expected_category"),
    [
        ("write", BrokenPipeError, errno.EPIPE, "ConnectionError"),
        ("flush", ConnectionResetError, errno.ECONNRESET, "ConnectionError"),
        ("flush", OSError, errno.EBADF, "OSError"),
    ],
)
def test_ipc_handler_logs_response_connection_drop(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stage: str,
    failure_type: type[OSError],
    error_number: int,
    expected_category: str,
) -> None:
    """Response failures emit bounded events and retain useful log severity."""

    class BrokenWriter:
        flush_called = False

        def write(self, payload: bytes) -> int:
            if stage == "write":
                raise failure_type(error_number, "secret-exception-detail")
            return len(payload)

        def flush(self) -> None:
            self.flush_called = True
            if stage == "flush":
                raise failure_type(error_number, "secret-exception-detail")

    writer = BrokenWriter()
    handler = SimpleNamespace(
        request=SimpleNamespace(settimeout=lambda _timeout: None),
        _read_request_payload=lambda: b"request",
        _read_deadline=time.monotonic() + 1.0,
        server=SimpleNamespace(outer=SimpleNamespace(timeout=1.0)),
        wfile=writer,
    )

    def response_pipeline(
        _server: object, _raw: bytes, _transport: str
    ) -> SimpleNamespace:
        return SimpleNamespace(response_bytes=b"reply", correlation_id="request-123")

    monkeypatch.setattr(_ipc_server, "_request_pipeline", response_pipeline)
    caplog.set_level("DEBUG", logger="cmd_mox.ipc.server")
    with _observability.capture_events() as events:
        _ipc_server._IPCHandler.handle(
            typ.cast("_ipc_server._IPCHandler", handler)
        )

    [event] = [
        event
        for event in events
        if event.operation == "ipc.server.connection"
        and event.outcome == "response_dropped"
    ]
    assert event.error_category == expected_category
    assert event.correlation_id == "request-123"
    assert writer.flush_called is (stage == "flush")
    if expected_category == "ConnectionError":
        _assert_disconnect_is_quiet(caplog)
    else:
        [error_record] = [
            record
            for record in caplog.records
            if record.name == "cmd_mox.ipc.server"
            and record.levelno == logging.ERROR
        ]
        assert error_record.getMessage() == "IPC reply write failed"
        assert error_record.exc_info is None
        assert "secret-exception-detail" not in caplog.text


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
        "_ipc_server._IPCHandler",
        types.SimpleNamespace(
            _read_request_payload=fail_read,
            server=types.SimpleNamespace(outer=types.SimpleNamespace(timeout=1.0)),
        ),
    )

    with _observability.capture_events() as events:
        _ipc_server._IPCHandler.handle(fake_handler)

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
            raise BrokenPipeError(errno.EPIPE, "secret-exception-detail")

        def flush(self) -> None:
            """Record whether the handler flushes after a failed write."""
            self.flush_called = True

    writer = BrokenWriter()
    fake_handler = typ.cast(
        "_ipc_server._IPCHandler",
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
        _ipc_server._IPCHandler.handle(fake_handler)

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

    emit_connect_retry = _client_events.emit_connect_retry

    def start_server_after_first_retry(
        transport: typ.Literal["unix", "named_pipe"],
        attempt: int,
        exc: Exception,
        correlation_id: str | None,
    ) -> None:
        emit_connect_retry(transport, attempt, exc, correlation_id)
        if attempt == 0:
            server.start()

    monkeypatch.setattr(
        _client_events, "emit_connect_retry", start_server_after_first_retry
    )
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="ls", args=[], stdin="", env={})
    try:
        # Leave room for the delayed server thread to be scheduled under xdist.
        if jitter is None:
            retry_config = RetryConfig(retries=20, backoff=0.01)
        else:
            retry_config = RetryConfig(retries=20, backoff=0.01, jitter=jitter)
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


_DISCONNECT_MESSAGE = "IPC client disconnected before reply was written"


class _DisconnectObserver(logging.Handler):
    """Signal when the reply-write failure is logged."""

    def __init__(self, disconnect_logged: threading.Event) -> None:
        super().__init__()
        self._disconnect_logged = disconnect_logged

    def emit(self, record: logging.LogRecord) -> None:
        if record.name != "cmd_mox.ipc.server" or record.levelno != logging.DEBUG:
            return
        if record.getMessage() == _DISCONNECT_MESSAGE:
            self._disconnect_logged.set()


def _assert_disconnect_is_quiet(caplog: pytest.LogCaptureFixture) -> None:
    """Assert a reply-write disconnect is logged only at DEBUG."""
    assert any(
        record.getMessage() == _DISCONNECT_MESSAGE and record.levelno == logging.DEBUG
        for record in caplog.records
    ), "The disconnect was not logged at DEBUG"
    server_records = [
        record for record in caplog.records if record.name == "cmd_mox.ipc.server"
    ]
    assert not any(record.levelno >= logging.ERROR for record in server_records), (
        "The client disconnect emitted an ERROR record"
    )
    assert not any(record.exc_info is not None for record in server_records), (
        "The client disconnect emitted an exception record"
    )


class _FailingReplyWriter:
    """Raise an I/O error at the selected reply-write stage."""

    def __init__(
        self,
        failure_point: typ.Literal["write", "flush"],
        error: OSError,
    ) -> None:
        self.failure_point = failure_point
        self.error = error
        self.write_called = False
        self.flush_called = False

    def write(self, response_bytes: bytes) -> int:
        self.write_called = True
        if self.failure_point == "write":
            raise self.error
        return len(response_bytes)

    def flush(self) -> None:
        self.flush_called = True
        if self.failure_point == "flush":
            raise self.error


@pytest.mark.parametrize("failure_point", ["write", "flush"])
def test_handler_logs_reply_write_oserror_at_debug(
    failure_point: typ.Literal["write", "flush"],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write and flush errors return quietly from the request handler."""
    caplog.set_level("DEBUG", logger="cmd_mox.ipc.server")
    caplog.clear()
    writer = _FailingReplyWriter(
        failure_point, BrokenPipeError(errno.EPIPE, "Broken pipe")
    )

    def response_pipeline(
        _server: object, _raw: bytes, _transport: str
    ) -> SimpleNamespace:
        return SimpleNamespace(response_bytes=b"response", correlation_id=None)

    monkeypatch.setattr(_ipc_server, "_request_pipeline", response_pipeline)
    handler = SimpleNamespace(
        request=SimpleNamespace(settimeout=lambda _timeout: None),
        _read_request_payload=lambda: b"request",
        _read_deadline=time.monotonic() + 1.0,
        wfile=writer,
        server=SimpleNamespace(outer=SimpleNamespace(timeout=1.0)),
    )

    _ipc_server._IPCHandler.handle(typ.cast("_ipc_server._IPCHandler", handler))

    assert writer.write_called, "The reply was not written"
    assert writer.flush_called is (failure_point == "flush"), (
        "Flush was called only when the write succeeded"
    )
    _assert_disconnect_is_quiet(caplog)


def test_handler_reports_non_disconnect_reply_write_oserror(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected reply-write errors stay visible without exception details."""
    caplog.set_level("DEBUG", logger="cmd_mox.ipc.server")
    caplog.clear()
    writer = _FailingReplyWriter("write", OSError(errno.EBADF, "Bad file descriptor"))

    def response_pipeline(
        _server: object, _raw: bytes, _transport: str
    ) -> SimpleNamespace:
        return SimpleNamespace(response_bytes=b"response", correlation_id=None)

    monkeypatch.setattr(_ipc_server, "_request_pipeline", response_pipeline)
    handler = SimpleNamespace(
        request=SimpleNamespace(settimeout=lambda _timeout: None),
        _read_request_payload=lambda: b"request",
        _read_deadline=time.monotonic() + 1.0,
        wfile=writer,
        server=SimpleNamespace(outer=SimpleNamespace(timeout=1.0)),
    )

    _ipc_server._IPCHandler.handle(typ.cast("_ipc_server._IPCHandler", handler))

    server_records = [
        record for record in caplog.records if record.name == "cmd_mox.ipc.server"
    ]
    error_records = [
        record for record in server_records if record.levelno == logging.ERROR
    ]
    assert len(error_records) == 1, "The unexpected write failure was not reported"
    assert error_records[0].getMessage() == "IPC reply write failed", (
        "The unexpected write error did not use its bounded category"
    )
    assert error_records[0].exc_info is None, "The error exposed exception details"
    assert "Bad file descriptor" not in caplog.text, (
        "The exception message was included in the log"
    )
    assert "Traceback" not in caplog.text, "The unexpected error emitted a traceback"


def test_server_logs_client_disconnect_during_reply_write(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client closing before the reply should not produce an error log."""
    disconnect_logged = threading.Event()
    handler_started = threading.Event()
    reply_allowed = threading.Event()

    def slow_handler(_invocation: Invocation) -> Response:
        handler_started.set()
        reply_allowed.wait(timeout=3.0)
        return Response(stdout="complete")

    observer = _DisconnectObserver(disconnect_logged)
    # Follow pytest's root capture handler so the event cannot race capture.
    root_logger = logging.getLogger()
    socket_path = tmp_path / "ipc.sock"

    with contextlib.ExitStack() as stack:
        root_logger.addHandler(observer)
        stack.callback(root_logger.removeHandler, observer)
        caplog.set_level("DEBUG", logger="cmd_mox.ipc.server")
        with IPCServer(
            socket_path, handlers=IPCHandlers(handler=slow_handler)
        ) as server:
            backend = server._server
            assert backend is not None, "The IPC server did not start"
            request_finished = threading.Event()
            shutdown_request = backend.shutdown_request

            def observe_request_shutdown(request: socket.socket) -> None:
                shutdown_request(request)
                request_finished.set()

            monkeypatch.setattr(backend, "shutdown_request", observe_request_shutdown)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.connect(str(socket_path))
                client.sendall(b'{"command":"ls","args":[],"stdin":"","env":{}}')
                client.shutdown(socket.SHUT_WR)
                assert handler_started.wait(timeout=3.0), (
                    "The server did not start handling the request"
                )

            reply_allowed.set()

            assert disconnect_logged.wait(timeout=3.0), (
                "The reply write did not report the disconnected client"
            )
            assert request_finished.wait(timeout=3.0), (
                "The request thread did not finish its socket cleanup"
            )

    _assert_disconnect_is_quiet(caplog)
    captured = capfd.readouterr()
    assert "Traceback" not in captured.err, "The server printed a traceback to stderr"
    assert "BrokenPipeError" not in captured.err, (
        "The server printed a BrokenPipeError to stderr"
    )
