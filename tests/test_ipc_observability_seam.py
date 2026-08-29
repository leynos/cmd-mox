"""Tests for the IPC observability seam and its client-side events."""

from __future__ import annotations

import dataclasses as dc
import json
import socket
import typing as typ

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import (
    Invocation,
    IPCHandlers,
    IPCServer,
    PassthroughResult,
    Response,
    RetryConfig,
    _client_events,
    _observability,
    _server_core,
    invoke_server,
    report_passthrough_result,
)
from cmd_mox.ipc.client import (
    _build_request_envelope,
    _connect_unix_with_retries,
    _ConnectionContext,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.requires_unix_sockets]


class _AlwaysRefusedSocket:
    """Socket double whose every connection attempt is refused."""

    attempts: int = 0

    def __init__(self, *_: object, **__: object) -> None:
        self.closed = False

    def settimeout(self, _timeout: float) -> None:
        """Accept the timeout the client configures."""

    def connect(self, _address: str) -> None:
        """Refuse the connection.

        Raises
        ------
        ConnectionRefusedError
            Always, so the retry seam runs to exhaustion.
        """
        type(self).attempts += 1
        raise ConnectionRefusedError

    def close(self) -> None:
        """Record that the client released the socket."""
        self.closed = True


def _events_for(
    events: list[_observability.IPCEvent], operation: str
) -> list[_observability.IPCEvent]:
    """Return captured *events* whose operation matches.

    Returns
    -------
    list[_observability.IPCEvent]
        The matching events in emission order.
    """
    return [event for event in events if event.operation == operation]


def test_event_as_extra_omits_inapplicable_fields() -> None:
    """Rendering an event should drop dimensions that do not apply."""
    event = _observability.IPCEvent(operation="ipc.test", outcome="success")

    assert event.as_extra() == {
        "operation": "ipc.test",
        "outcome": "success",
    }, "Inapplicable fields must be omitted"


def test_emit_counts_events_by_bounded_key() -> None:
    """The registry should count events by operation, transport, and outcome."""
    _observability.reset_counters()
    event = _observability.IPCEvent(
        operation="ipc.test",
        transport="unix",
        outcome="success",
        correlation_id="corr-1",
    )

    _observability.emit(event)
    _observability.emit(dc.replace(event, correlation_id="corr-2"))

    key = _observability.EventKey("ipc.test", "unix", "success")
    assert _observability.counter_snapshot()[key] == 2, "Counter must aggregate"
    _observability.reset_counters()
    assert _observability.counter_snapshot() == {}, "Reset must clear counters"


def test_capture_events_collects_emissions() -> None:
    """The capture context manager should collect events without a collector."""
    with _observability.capture_events() as events:
        _observability.emit(_observability.IPCEvent(operation="ipc.test"))
    _observability.emit(_observability.IPCEvent(operation="ipc.test"))

    assert len(events) == 1, "Only in-context emissions are captured"


def test_connect_retry_events_carry_correlation_and_category(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each failed connect attempt should emit a bounded retry event."""
    _AlwaysRefusedSocket.attempts = 0
    monkeypatch.setattr(socket, "socket", _AlwaysRefusedSocket)
    context = _ConnectionContext(
        timeout=0.1,
        retry_config=RetryConfig(retries=2, backoff=0.0, jitter=0.0),
        correlation_id="corr-retry",
    )

    with (
        _observability.capture_events() as events,
        pytest.raises(ConnectionRefusedError),
    ):
        _connect_unix_with_retries(tmp_path / "ipc.sock", context)

    retries = _events_for(events, _client_events.CONNECT_RETRY_OPERATION)
    assert len(retries) == 2, "One event per failed attempt"
    assert [event.attempt for event in retries] == [1, 2], "Attempts are 1-based"
    categories = {event.error_category for event in retries}
    assert categories == {"ConnectionRefusedError"}, "Stable error category expected"
    ids = {event.correlation_id for event in retries}
    assert ids == {"corr-retry"}, "Retry events must carry the correlation id"
    transports = {event.transport for event in retries}
    assert transports == {"unix"}, "Retry events must name the transport"


def test_client_and_server_share_one_correlation_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A client-generated identifier should reach the server dispatch record."""
    socket_path = tmp_path / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="git", args=["status"], stdin="", env={})

    with IPCServer(socket_path), _observability.capture_events() as events:
        response = invoke_server(invocation, timeout=5.0)

    assert response.stdout == "git", "Server did not echo the command"
    started, succeeded = _events_for(events, _client_events.REQUEST_OPERATION)
    assert started.outcome == "started", "First client event must be the start"
    assert started.message_size is not None, "Start event must record the size"
    assert succeeded.outcome == "success", "Second client event must be the success"
    assert succeeded.duration_ms is not None, "Success event must record a duration"
    [dispatch] = _events_for(events, _server_core.DISPATCH_OPERATION)
    assert dispatch.correlation_id == succeeded.correlation_id, (
        "Client and server must share one correlation id"
    )
    assert dispatch.transport == "unix", "Dispatch must record the transport"


def test_client_failure_event_records_error_category(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed request should emit an error event with a bounded category."""
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(tmp_path / "missing.sock"))
    invocation = Invocation(command="git", args=[], stdin="", env={})
    retry_config = RetryConfig(retries=1, backoff=0.0, jitter=0.0)

    with (
        _observability.capture_events() as events,
        pytest.raises(FileNotFoundError),
    ):
        invoke_server(invocation, timeout=0.5, retry_config=retry_config)

    _started, failed = _events_for(events, _client_events.REQUEST_OPERATION)
    assert failed.outcome == "error", "Failures must emit an error outcome"
    assert failed.error_category == "FileNotFoundError", "Wrong error category"
    assert failed.correlation_id is not None, "Failures must stay correlated"


def test_passthrough_result_preserves_its_invocation_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passthrough requests correlate on their existing invocation identifier."""
    socket_path = tmp_path / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    result = PassthroughResult(
        invocation_id="passthrough-123",
        stdout="secret-stdout",
        stderr="secret-stderr",
        exit_code=0,
    )
    captured: list[PassthroughResult] = []

    def handler(passthrough: PassthroughResult) -> Response:
        captured.append(passthrough)
        return Response(stdout="ok")

    handlers = IPCHandlers(passthrough_handler=handler)
    with (
        IPCServer(socket_path, handlers=handlers),
        _observability.capture_events() as events,
    ):
        report_passthrough_result(result, timeout=5.0)

    assert captured, "Passthrough handler was not invoked"
    [dispatch] = _events_for(events, _server_core.DISPATCH_OPERATION)
    assert dispatch.correlation_id == "passthrough-123", "Wrong correlation id"


def test_envelope_round_trip_reaches_the_model_unchanged() -> None:
    """The envelope fields must not leak into the validated model."""
    invocation = Invocation(command="git", args=["status"], stdin="", env={})
    payload, correlation_id = _build_request_envelope(
        _server_core.KIND_INVOCATION, invocation.to_dict()
    )

    parsed = _server_core._parse_payload(payload)

    assert parsed is not None, "Envelope did not parse"
    assert parsed.correlation_id == correlation_id, "Envelope id was not carried"
    validated = parsed.validate()
    assert isinstance(validated, Invocation), "Envelope broke model construction"
    assert validated.command == "git", "Model body was not preserved"
    assert json.loads(payload)["correlation_id"] == correlation_id, "Wire field missing"


def test_over_long_invocation_id_yields_a_fresh_bounded_correlation_id() -> None:
    """An unbounded model identifier is replaced, never reused verbatim."""
    over_long = "x" * (_server_core.MAX_CORRELATION_ID_LENGTH + 1)

    correlation_id = _client_events.resolve_correlation_id({"invocation_id": over_long})

    assert correlation_id != over_long, "Over-long identifier must not be reused"
    assert len(correlation_id) <= _server_core.MAX_CORRELATION_ID_LENGTH, (
        "Minted identifier is not bounded"
    )
