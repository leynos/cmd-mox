"""Regression tests for bounded IPC connection outcomes."""

from __future__ import annotations

import dataclasses as dc
import errno
import json
import logging
import time
import types
import typing as typ
from types import SimpleNamespace

import pytest

from cmd_mox.ipc import _observability
from cmd_mox.ipc import server as _ipc_server

pytestmark = pytest.mark.requires_unix_sockets


@dc.dataclass(frozen=True)
class _ResponseDropScenario:
    """Describe one response-write failure and its expected outcome."""

    stage: typ.Literal["write", "flush"]
    failure_type: type[OSError]
    error_number: int
    expected_category: str


class _FailingResponseWriter:
    """Fail at a selected reply-write stage while tracking flush calls."""

    def __init__(self, scenario: _ResponseDropScenario) -> None:
        """Store the selected failure and initialize flush tracking."""
        self._scenario = scenario
        self.flush_called = False

    def write(self, payload: bytes) -> int:
        """Fail at the configured write stage or accept the payload.

        Returns
        -------
        int
            Number of payload bytes accepted when the write succeeds.
        """
        if self._scenario.stage == "write":
            raise self._failure()
        return len(payload)

    def flush(self) -> None:
        """Record the flush and fail when the scenario selects this stage."""
        self.flush_called = True
        if self._scenario.stage == "flush":
            raise self._failure()

    def _failure(self) -> OSError:
        """Create the configured error with a deliberately sensitive detail.

        Returns
        -------
        OSError
            The configured failure with its test-only sensitive detail.
        """
        return self._scenario.failure_type(
            self._scenario.error_number, "secret-exception-detail"
        )


def _response_pipeline(
    _server: object, _raw: bytes, _transport: str
) -> SimpleNamespace:
    """Return a response double that carries the expected request ID.

    Returns
    -------
    SimpleNamespace
        A successful response with the expected correlation identifier.
    """
    return SimpleNamespace(response_bytes=b"reply", correlation_id="request-123")


def _response_drop_handler(writer: _FailingResponseWriter) -> SimpleNamespace:
    """Build the smallest handler double for a simulated response drop.

    Returns
    -------
    SimpleNamespace
        A handler double configured with *writer*.
    """
    return SimpleNamespace(
        request=SimpleNamespace(settimeout=lambda _timeout: None),
        _read_request_payload=lambda: b"request",
        _read_deadline=time.monotonic() + 1.0,
        server=SimpleNamespace(outer=SimpleNamespace(timeout=1.0)),
        wfile=writer,
    )


def _assert_disconnect_logged_quietly(caplog: pytest.LogCaptureFixture) -> None:
    """Check that a disconnected client does not produce an error log."""
    assert any(
        record.getMessage() == "IPC client disconnected before reply was written"
        and record.levelno == logging.DEBUG
        for record in caplog.records
    ), "Disconnect was not logged quietly"
    assert not any(record.levelno >= logging.ERROR for record in caplog.records), (
        "A disconnect emitted an error log"
    )
    assert not any(
        record.name == "cmd_mox.ipc.server" and record.exc_info is not None
        for record in caplog.records
    ), "A disconnect emitted an exception record"


def _assert_unexpected_write_error_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check that unexpected write errors stay visible without details."""
    [error_record] = [
        record
        for record in caplog.records
        if record.name == "cmd_mox.ipc.server" and record.levelno == logging.ERROR
    ]
    assert error_record.getMessage() == "IPC reply write failed", (
        "Unexpected write error did not use its bounded message"
    )
    assert error_record.exc_info is None, "Write error exposed exception details"
    assert "secret-exception-detail" not in caplog.text, (
        "Exception detail leaked into logs"
    )


def _assert_response_drop_result(
    events: list[_observability.IPCEvent],
    caplog: pytest.LogCaptureFixture,
    writer: _FailingResponseWriter,
    scenario: _ResponseDropScenario,
) -> None:
    """Check the bounded event and diagnostic policy for a failed reply."""
    [event] = [
        item
        for item in events
        if item.operation == "ipc.server.connection"
        and item.outcome == "response_dropped"
    ]
    assert event.error_category == scenario.expected_category, (
        "Response drop reported the wrong bounded error category"
    )
    assert event.correlation_id == "request-123", "Response drop lost correlation"
    assert writer.flush_called is (scenario.stage == "flush"), (
        "Flush was called inconsistently with the failing stage"
    )
    if scenario.expected_category == "ConnectionError":
        _assert_disconnect_logged_quietly(caplog)
    else:
        _assert_unexpected_write_error_logged(caplog)


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
        operation="ipc.server.connection",
        transport="unix",
        selection_mode=None,
        outcome="response_dropped",
        cache_name=None,
        cache_outcome=None,
        error_category=None,
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
        operation="ipc.server.connection",
        transport="unix",
        selection_mode=None,
        outcome="response_dropped",
        cache_name=None,
        cache_outcome=None,
        error_category=None,
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


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            _ResponseDropScenario(
                "write", BrokenPipeError, errno.EPIPE, "ConnectionError"
            ),
            id="broken-pipe-during-write",
        ),
        pytest.param(
            _ResponseDropScenario(
                "flush", ConnectionResetError, errno.ECONNRESET, "ConnectionError"
            ),
            id="connection-reset-during-flush",
        ),
        pytest.param(
            _ResponseDropScenario("flush", OSError, errno.EBADF, "OSError"),
            id="unexpected-flush-error",
        ),
    ],
)
def test_ipc_handler_logs_response_connection_drop(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    scenario: _ResponseDropScenario,
) -> None:
    """Response failures emit bounded events and retain useful log severity."""
    writer = _FailingResponseWriter(scenario)
    handler = _response_drop_handler(writer)
    monkeypatch.setattr(_ipc_server, "_request_pipeline", _response_pipeline)
    caplog.set_level("DEBUG", logger="cmd_mox.ipc.server")
    with _observability.capture_events() as events:
        _ipc_server._IPCHandler.handle(typ.cast("_ipc_server._IPCHandler", handler))

    _assert_response_drop_result(events, caplog, writer, scenario)


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
