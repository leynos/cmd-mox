"""Tests covering bounded IPC dispatch observability records."""

from __future__ import annotations

import json
import typing as typ

import pytest

from cmd_mox.ipc import (
    Invocation,
    IPCHandlers,
    IPCServer,
    PassthroughResult,
    Response,
    _server_core,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path


pytestmark = [pytest.mark.requires_unix_sockets]

# Structured-field names that must never appear on an emitted record. The
# stdlib ``LogRecord`` owns ``args``, so command arguments are guarded by the
# value scan in :func:`_assert_bounded_fields` instead.
_NEVER_LOGGED_KEYS: frozenset[str] = frozenset({
    "command",
    "env",
    "payload",
    "socket_path",
    "stderr",
    "stdin",
    "stdout",
})


@pytest.fixture
def echo_handler() -> cabc.Callable[[Invocation], Response]:
    """Return a handler that echoes the command name.

    Returns
    -------
    cabc.Callable[[Invocation], Response]
        A handler returning the invoked command name as stdout.
    """

    def handler(invocation: Invocation) -> Response:
        return Response(stdout=invocation.command)

    return handler


@pytest.fixture
def passthrough_handler() -> cabc.Callable[[PassthroughResult], Response]:
    """Return a handler that returns a fixed passthrough response.

    Returns
    -------
    cabc.Callable[[PassthroughResult], Response]
        A handler returning a fixed "passthrough" response.
    """

    def handler(_result: PassthroughResult) -> Response:
        return Response(stdout="passthrough")

    return handler


def _dispatch_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    """Return bounded IPC dispatch records captured by *caplog*.

    Returns
    -------
    list[dict[str, object]]
        Dispatch outcome records emitted by the shared pipeline.
    """
    return [
        record.__dict__
        for record in caplog.records
        if record.__dict__.get("operation") == "ipc.dispatch"
    ]


def _assert_bounded_duration(event: dict[str, object]) -> None:
    """Assert the dispatch record carries a plausible scoped duration."""
    duration = event.get("duration_ms")
    assert isinstance(duration, float), "duration_ms must be a float"
    assert duration >= 0.0, "duration_ms must be non-negative"


def _assert_bounded_fields(
    event: dict[str, object], secrets: cabc.Sequence[str]
) -> None:
    """Assert only allowed metadata keys carry payload-free values.

    The dispatch record must never surface command text, arguments, streams, or
    environment data, so each known-secret value is checked against every
    structured field the emitter contributes.
    """
    allowed = {
        "operation",
        "transport",
        "kind",
        "outcome",
        "duration_ms",
        "invocation_id",
        "correlation_id",
        "error_category",
    }
    emitted = {key: event[key] for key in allowed if key in event}
    rendered = " ".join(str(value) for value in emitted.values())
    rendered += f" {event.get('msg', '')}"
    for secret in secrets:
        assert secret not in rendered, f"{secret!r} leaked into dispatch metadata"
    banned = _NEVER_LOGGED_KEYS & set(event)
    assert not banned, f"payload fields leaked as metadata: {sorted(banned)}"


def test_request_pipeline_logs_successful_invocation_dispatch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Successful invocation dispatch should emit bounded metadata."""
    caplog.set_level("INFO", logger="cmd_mox.ipc._server_core")
    ipc_server = IPCServer(tmp_path / "ipc.sock")

    response = _server_core._request_pipeline(
        ipc_server,
        json.dumps({
            "kind": _server_core.KIND_INVOCATION,
            "command": "git",
            "args": ["status"],
            "stdin": "secret-stdin",
            "env": {"TOKEN": "secret-env"},
        }).encode(),
    )

    assert response is not None, "Successful dispatch did not return a response"
    [event] = _dispatch_events(caplog)
    assert event["operation"] == "ipc.dispatch", "Wrong operation"
    assert event["kind"] == _server_core.KIND_INVOCATION, "Wrong request kind"
    assert event["outcome"] == "success", "Wrong dispatch outcome"
    assert "invocation_id" not in event, "Invocation ID must be omitted"
    assert "error_category" not in event, "Success must not include an error"
    _assert_bounded_duration(event)
    _assert_bounded_fields(event, ["git", "status", "secret-stdin", "secret-env"])


def test_request_pipeline_logs_successful_passthrough_dispatch(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    echo_handler: cabc.Callable[[Invocation], Response],
    passthrough_handler: cabc.Callable[[PassthroughResult], Response],
) -> None:
    """Successful passthrough dispatch should include its invocation ID."""
    caplog.set_level("INFO", logger="cmd_mox.ipc._server_core")
    ipc_server = IPCServer(
        tmp_path / "ipc.sock",
        handlers=IPCHandlers(
            handler=echo_handler,
            passthrough_handler=passthrough_handler,
        ),
    )

    response = _server_core._request_pipeline(
        ipc_server,
        json.dumps({
            "kind": _server_core.KIND_PASSTHROUGH_RESULT,
            "invocation_id": "passthrough-123",
            "stdout": "secret-stdout",
            "stderr": "secret-stderr",
            "exit_code": 0,
        }).encode(),
    )

    assert response is not None, "Successful dispatch did not return a response"
    [event] = _dispatch_events(caplog)
    assert event["kind"] == _server_core.KIND_PASSTHROUGH_RESULT, "Wrong kind"
    assert event["invocation_id"] == "passthrough-123", "Wrong ID"
    assert event["outcome"] == "success", "Wrong dispatch outcome"
    assert "error_category" not in event, "Success must not include an error"
    _assert_bounded_duration(event)
    _assert_bounded_fields(event, ["secret-stdout", "secret-stderr"])


def test_request_pipeline_logs_invalid_request(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Invalid requests should emit a bounded validation outcome."""
    caplog.set_level("INFO", logger="cmd_mox.ipc._server_core")
    ipc_server = IPCServer(tmp_path / "ipc.sock")

    response = _server_core._request_pipeline(
        ipc_server,
        json.dumps({"kind": _server_core.KIND_INVOCATION}).encode(),
    )

    assert response is None, "Invalid request unexpectedly produced a response"
    [event] = _dispatch_events(caplog)
    assert event["kind"] == _server_core.KIND_INVOCATION, "Wrong request kind"
    assert event["outcome"] == "invalid_request", "Wrong outcome"
    assert event["error_category"] == "ValidationError", "Wrong error"
    assert "invocation_id" not in event, "Invocation ID must be omitted"
    _assert_bounded_duration(event)


def test_request_pipeline_logs_handler_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Handler failures should emit only a bounded error category."""
    caplog.set_level("INFO", logger="cmd_mox.ipc._server_core")

    def failing_handler(_invocation: Invocation) -> Response:
        msg = "handler failure"
        raise RuntimeError(msg)

    ipc_server = IPCServer(
        tmp_path / "ipc.sock",
        handlers=IPCHandlers(handler=failing_handler),
    )
    response = _server_core._request_pipeline(
        ipc_server,
        json.dumps({
            "kind": _server_core.KIND_INVOCATION,
            "command": "git",
            "args": [],
            "stdin": "",
            "env": {},
        }).encode(),
    )

    assert response is not None, "Handler failure did not return an error response"
    [event] = _dispatch_events(caplog)
    assert event["kind"] == _server_core.KIND_INVOCATION, "Wrong request kind"
    assert event["outcome"] == "handler_error", "Wrong outcome"


def test_request_pipeline_records_transport_and_correlation_id(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The dispatch record should carry the transport and envelope identifier."""
    caplog.set_level("INFO", logger="cmd_mox.ipc._server_core")
    ipc_server = IPCServer(tmp_path / "ipc.sock")

    response = _server_core._request_pipeline(
        ipc_server,
        json.dumps({
            "kind": _server_core.KIND_INVOCATION,
            "correlation_id": "corr-abc",
            "command": "git",
            "args": ["status"],
            "stdin": "secret-stdin",
            "env": {"TOKEN": "secret-env"},
        }).encode(),
        "unix",
    )

    assert response is not None, "Successful dispatch did not return a response"
    [event] = _dispatch_events(caplog)
    assert event["transport"] == "unix", "Wrong transport"
    assert event["correlation_id"] == "corr-abc", "Wrong correlation id"
    _assert_bounded_fields(event, ["git", "status", "secret-stdin", "secret-env"])


def test_request_pipeline_omits_correlation_id_for_legacy_invocations(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Older shims omit the envelope field and invocations carry no identifier."""
    caplog.set_level("INFO", logger="cmd_mox.ipc._server_core")
    ipc_server = IPCServer(tmp_path / "ipc.sock")

    _server_core._request_pipeline(
        ipc_server,
        json.dumps({
            "kind": _server_core.KIND_INVOCATION,
            "command": "git",
            "args": [],
            "stdin": "",
            "env": {},
        }).encode(),
    )

    [event] = _dispatch_events(caplog)
    assert "correlation_id" not in event, "No identifier may be manufactured"
    assert "transport" not in event, "Unknown transport must be omitted"


def test_request_pipeline_falls_back_to_model_invocation_id(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    echo_handler: cabc.Callable[[Invocation], Response],
    passthrough_handler: cabc.Callable[[PassthroughResult], Response],
) -> None:
    """Without an envelope field the validated model supplies the identifier."""
    caplog.set_level("INFO", logger="cmd_mox.ipc._server_core")
    ipc_server = IPCServer(
        tmp_path / "ipc.sock",
        handlers=IPCHandlers(
            handler=echo_handler,
            passthrough_handler=passthrough_handler,
        ),
    )

    _server_core._request_pipeline(
        ipc_server,
        json.dumps({
            "kind": _server_core.KIND_PASSTHROUGH_RESULT,
            "invocation_id": "passthrough-123",
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
        }).encode(),
    )

    [event] = _dispatch_events(caplog)
    assert event["correlation_id"] == "passthrough-123", "Wrong correlation id"
    assert event["invocation_id"] == "passthrough-123", "Wrong invocation id"


@pytest.mark.parametrize(
    "correlation_id",
    [
        pytest.param(17, id="not-a-string"),
        pytest.param("", id="empty"),
        pytest.param("x" * 65, id="over-long"),
    ],
)
def test_parse_payload_rejects_unbounded_correlation_ids(
    correlation_id: object,
) -> None:
    """Unusable envelope identifiers are dropped rather than logged."""
    parsed = _server_core._parse_payload(
        json.dumps({
            "kind": _server_core.KIND_INVOCATION,
            "correlation_id": correlation_id,
            "command": "git",
            "args": [],
            "stdin": "",
            "env": {},
        }).encode()
    )

    assert parsed is not None, "Payload should still parse"
    assert parsed.correlation_id is None, "Unbounded identifier must be dropped"
    assert "correlation_id" not in parsed.payload, "Envelope field must be stripped"
