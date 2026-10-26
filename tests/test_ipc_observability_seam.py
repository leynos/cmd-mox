"""Tests for the IPC observability seam and its client-side events."""

from __future__ import annotations

import dataclasses as dc
import json
import socket
import typing as typ
import uuid

import pytest

from cmd_mox._correlation import MAX_CORRELATION_ID_LENGTH, bounded_correlation_id
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

_EXPECTED_BACKEND_ERROR_CATEGORIES = frozenset({
    "unsupported_platform",
    "windows_unsupported",
    "os_error",
    "runtime_error",
    "type_error",
    "value_error",
})
_EXPECTED_NAMED_PIPE_ERROR_CATEGORIES = frozenset({
    "ClientLimitReached",
    "ThreadStartFailed",
    "ServerStopping",
    "PipeMessageTooLargeError",
    "BrokenPipe",
    "NoData",
    "PipeError",
    "ReadTimeout",
    "ServerStopped",
})


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
    """Counter keys retain selection/cache dimensions and ignore correlation."""
    _observability.reset_counters()
    event = _observability.IPCEvent(
        operation="ipc.test",
        transport="unix",
        selection_mode="native",
        outcome="success",
        cache_name="unix_server_base",
        cache_outcome="miss",
        correlation_id="corr-1",
    )
    events = (
        event,
        dc.replace(event, correlation_id="corr-2"),
        dc.replace(event, cache_outcome="hit", correlation_id="corr-3"),
        dc.replace(event, selection_mode="compatibility", correlation_id="corr-4"),
        dc.replace(event, cache_name="inner_server_class", correlation_id="corr-5"),
    )
    expected_counters = {
        _observability.EventKey(
            "ipc.test",
            "unix",
            "native",
            "success",
            "unix_server_base",
            "miss",
            None,
        ): 2,
        _observability.EventKey(
            "ipc.test",
            "unix",
            "native",
            "success",
            "unix_server_base",
            "hit",
            None,
        ): 1,
        _observability.EventKey(
            "ipc.test",
            "unix",
            "compatibility",
            "success",
            "unix_server_base",
            "miss",
            None,
        ): 1,
        _observability.EventKey(
            "ipc.test",
            "unix",
            "native",
            "success",
            "inner_server_class",
            "miss",
            None,
        ): 1,
    }
    try:
        for event in events:
            _observability.emit(event)

        assert _observability.counter_snapshot() == expected_counters, (
            "Counters must distinguish bounded selection/cache dimensions and "
            "aggregate equivalent events"
        )
    finally:
        _observability.reset_counters()


def test_counter_key_omits_unbounded_message_kinds_and_error_categories() -> None:
    """Dynamic exception classes and message kinds must not expand counters."""
    _observability.reset_counters()
    try:
        for index in range(4):
            _observability.emit(
                _observability.IPCEvent(
                    operation="ipc.dispatch",
                    transport="unix",
                    kind=f"plugin_kind_{index}",
                    outcome="handler_error",
                    error_category=f"PluginException{index}",
                )
            )

        counters = _observability.counter_snapshot()

        assert len(counters) == 1, "Unbounded values must collapse to one counter key"
        [key] = counters
        assert key.error_category is None, (
            "Dynamic exception names must not label counters"
        )
        assert counters[key] == 4, "All equivalent dispatch errors must aggregate"
    finally:
        _observability.reset_counters()


def test_counter_key_preserves_fixed_backend_error_categories() -> None:
    """Keep bounded backend error labels available for diagnosis."""
    categories = _EXPECTED_BACKEND_ERROR_CATEGORIES
    _observability.reset_counters()
    try:
        for error_category in categories:
            _observability.emit(
                _observability.IPCEvent(
                    operation="ipc.backend_selection",
                    transport="unix",
                    selection_mode="native",
                    outcome="backend_construction_failed",
                    cache_name="inner_server_class",
                    cache_outcome="miss",
                    error_category=error_category,
                )
            )

        counters = _observability.counter_snapshot()

        assert {key.error_category for key in counters} == categories, (
            "Fixed backend categories must remain separate counter dimensions"
        )
    finally:
        _observability.reset_counters()


def test_counter_key_preserves_fixed_named_pipe_error_categories() -> None:
    """Keep closed named-pipe failure labels available for diagnosis."""
    categories = _EXPECTED_NAMED_PIPE_ERROR_CATEGORIES
    _observability.reset_counters()
    try:
        for error_category in categories:
            _observability.emit(
                _observability.IPCEvent(
                    operation="ipc.named_pipe.worker",
                    transport="named_pipe",
                    outcome="rejected",
                    error_category=error_category,
                )
            )

        counter_categories = {
            key.error_category for key in _observability.counter_snapshot()
        }

        assert counter_categories == categories, (
            "Fixed named-pipe categories must remain separate counter dimensions"
        )
    finally:
        _observability.reset_counters()


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


def test_passthrough_report_failure_emits_correlated_client_event(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed passthrough report remains visible through the client seam."""
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(tmp_path / "missing.sock"))
    result = PassthroughResult(
        invocation_id="passthrough-123",
        stdout="secret-stdout",
        stderr="secret-stderr",
        exit_code=2,
    )
    retry_config = RetryConfig(retries=1, backoff=0.0, jitter=0.0)
    counters_before = _observability.counter_snapshot()

    with (
        _observability.capture_events() as events,
        pytest.raises(FileNotFoundError),
    ):
        report_passthrough_result(result, timeout=0.5, retry_config=retry_config)

    started, failed = _events_for(events, _client_events.REQUEST_OPERATION)
    assert started.kind == "passthrough-result", "Wrong passthrough event kind"
    assert started.outcome == "started", "Missing passthrough start event"
    assert failed.kind == "passthrough-result", "Wrong failed event kind"
    assert failed.outcome == "error", "Failed report event has wrong outcome"
    assert failed.error_category == "FileNotFoundError", "Wrong report error category"
    assert failed.correlation_id == result.invocation_id, (
        "Passthrough report lost its validated correlation id"
    )
    assert failed.duration_ms is not None, (
        "Passthrough report failure lost its duration"
    )
    assert failed.duration_ms >= 0, "Passthrough report failure duration is negative"
    counts_after = _observability.counter_snapshot()
    assert (
        counts_after.get(failed.key(), 0) == counters_before.get(failed.key(), 0) + 1
    ), "Passthrough report failure did not increment its bounded event counter"


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


def _assert_minted_identifier(value: str, rejected: object) -> None:
    """Assert *value* is a fresh, bounded, log-safe minted identifier."""
    assert value, "Minted identifier is empty"
    assert value != rejected, "Rejected identifier was reused"
    assert len(value) <= MAX_CORRELATION_ID_LENGTH, "Not bounded"
    assert uuid.UUID(value), "Minted identifier is not a UUID"
    assert bounded_correlation_id(value) == value, "Not log-safe"


@pytest.mark.parametrize(
    ("data", "rejected"),
    [
        pytest.param({}, None, id="absent"),
        pytest.param({"invocation_id": ""}, "", id="empty"),
        pytest.param(
            {"invocation_id": "x" * (MAX_CORRELATION_ID_LENGTH + 1)},
            "x" * (MAX_CORRELATION_ID_LENGTH + 1),
            id="over-long",
        ),
        pytest.param(
            {"invocation_id": "id\nWARNING fake"}, "id\nWARNING fake", id="newline"
        ),
        pytest.param(
            {"invocation_id": "id\x1b[31mred"}, "id\x1b[31mred", id="ansi-escape"
        ),
        pytest.param({"invocation_id": "id\x00null"}, "id\x00null", id="control-char"),
    ],
)
def test_unusable_invocation_id_yields_a_fresh_bounded_correlation_id(
    data: dict[str, object], rejected: object
) -> None:
    """An unusable model identifier is replaced by a fresh opaque UUID."""
    first = _client_events.resolve_correlation_id(data)
    second = _client_events.resolve_correlation_id(data)

    _assert_minted_identifier(first, rejected)
    _assert_minted_identifier(second, rejected)
    assert first != second, "Minted identifiers must be distinct"


def test_usable_invocation_id_is_reused_verbatim() -> None:
    """A conforming identifier is carried through, not replaced."""
    existing = uuid.uuid4().hex

    resolved = _client_events.resolve_correlation_id({"invocation_id": existing})

    assert resolved == existing, "A usable identifier must be reused"
