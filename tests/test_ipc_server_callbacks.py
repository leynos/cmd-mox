"""Tests covering :class:`cmd_mox.ipc.IPCServer` callback behaviour."""

from __future__ import annotations

import dataclasses as dc
import json
import threading
import typing as typ

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import (
    CallbackIPCServer,
    Invocation,
    IPCHandlers,
    IPCServer,
    PassthroughResult,
    Response,
    TimeoutConfig,
    invoke_server,
    report_passthrough_result,
    server,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion

type _RequestPayload = dict[
    str, str | int | float | bool | list[str] | dict[str, str] | None
]


pytestmark = [pytest.mark.requires_unix_sockets]


class _OverridingIPCServer(IPCServer):
    """IPC server whose hook overrides prove request dispatch remains virtual."""

    def handle_invocation(self, invocation: Invocation) -> Response:
        """Return a response that identifies the overridden invocation hook."""  # ruff: ignore[docstring-missing-returns] - test callback; return contract is test-local
        return Response(stdout=f"override:{invocation.command}")

    def handle_passthrough_result(self, result: PassthroughResult) -> Response:
        """Return a response that identifies the overridden passthrough hook."""  # ruff: ignore[docstring-missing-returns] - test callback; return contract is test-local
        return Response(stdout=f"override:{result.invocation_id}")


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


@dc.dataclass(slots=True)
class TimeoutTestCase:
    """Test case configuration for timeout validation."""

    timeouts_arg: TimeoutConfig | None
    expected_timeout: float
    expected_accept_timeout: float


@pytest.fixture
def echo_handler() -> cabc.Callable[[Invocation], Response]:
    """Return a handler that echoes the command name."""  # ruff: ignore[docstring-missing-returns] - pytest fixture; return contract is test-local

    def handler(invocation: Invocation) -> Response:
        return Response(stdout=invocation.command)

    return handler


@pytest.fixture
def passthrough_handler() -> cabc.Callable[[PassthroughResult], Response]:
    """Return a handler that returns a fixed passthrough response."""  # ruff: ignore[docstring-missing-returns] - pytest fixture; return contract is test-local

    def handler(_result: PassthroughResult) -> Response:
        return Response(stdout="passthrough")

    return handler


@pytest.mark.usefixtures("tmp_path")
def test_ipcserver_default_invocation_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IPCServer should retain the legacy echo behaviour without a handler."""
    socket_path = tmp_path / "ipc.sock"

    with IPCServer(socket_path):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="cmd", args=["--flag"], stdin="", env={})
        response = invoke_server(invocation, timeout=1.0)

    assert response.stdout == "cmd", "Assertion failed"
    assert response.stderr == "", "Assertion failed"
    assert response.exit_code == 0, "Assertion failed"


@pytest.mark.usefixtures("tmp_path")
def test_ipcserver_invocation_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IPCServer should delegate invocations to the configured handler."""
    socket_path = tmp_path / "ipc.sock"
    seen: list[Invocation] = []

    def handler(invocation: Invocation) -> Response:
        """Record invocations and return a distinctive response."""  # ruff: ignore[docstring-missing-returns] - test callback; return contract is test-local
        seen.append(invocation)
        return Response(stdout="handled", stderr="err", exit_code=2)

    with IPCServer(socket_path, handlers=IPCHandlers(handler=handler)):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="cmd", args=["--flag"], stdin="", env={})
        response = invoke_server(invocation, timeout=1.0)

    assert seen, "Assertion failed"
    assert seen[0].command == "cmd", "Assertion failed"
    assert response.stdout == "handled", "Assertion failed"
    assert response.stderr == "err", "Assertion failed"
    assert response.exit_code == 2, "Assertion failed"


@pytest.mark.usefixtures("tmp_path")
def test_ipcserver_handler_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IPCServer should surface handler exceptions via error responses."""
    socket_path = tmp_path / "ipc.sock"

    def handler(_invocation: Invocation) -> Response:
        msg = "handler failed"
        raise RuntimeError(msg)

    with IPCServer(socket_path, handlers=IPCHandlers(handler=handler)):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="cmd", args=[], stdin="", env={})
        response = invoke_server(invocation, timeout=1.0)

    assert response.exit_code == 1, "Assertion failed"
    assert "handler failed" in response.stderr, "Assertion failed"
    assert response.stdout == "", "Assertion failed"


@pytest.mark.usefixtures("tmp_path")
def test_ipcserver_default_passthrough_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passthroughs should raise when no handler is configured."""
    socket_path = tmp_path / "ipc.sock"

    with IPCServer(socket_path):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        result = PassthroughResult(
            invocation_id="123",
            stdout="out",
            stderr="err",
            exit_code=0,
        )
        response = report_passthrough_result(result, timeout=1.0)

    assert response.exit_code == 1, "Assertion failed"
    assert "Unhandled passthrough result for 123" in response.stderr, "Assertion failed"
    assert response.stdout == "", "Assertion failed"


@pytest.mark.usefixtures("tmp_path")
def test_ipcserver_passthrough_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    echo_handler: cabc.Callable[[Invocation], Response],
) -> None:
    """IPCServer should delegate passthrough results when a handler is provided."""
    socket_path = tmp_path / "ipc.sock"
    seen: list[PassthroughResult] = []

    def passthrough_handler(result: PassthroughResult) -> Response:
        """Capture passthrough results and return a custom response."""  # ruff: ignore[docstring-missing-returns] - test callback; return contract is test-local
        seen.append(result)
        return Response(stdout="passthrough", exit_code=5)

    with IPCServer(
        socket_path,
        handlers=IPCHandlers(
            handler=echo_handler,
            passthrough_handler=passthrough_handler,
        ),
    ):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        result = PassthroughResult(
            invocation_id="123",
            stdout="out",
            stderr="err",
            exit_code=0,
        )
        response = report_passthrough_result(result, timeout=1.0)

    assert seen, "Assertion failed"
    assert seen[0].invocation_id == "123", "Assertion failed"
    assert response.stdout == "passthrough", "Assertion failed"
    assert response.exit_code == 5, "Assertion failed"


def test_handle_invocation_default(tmp_path: Path) -> None:
    """Direct invocation handling should echo when no handler is set."""
    server = IPCServer(tmp_path / "ipc.sock")
    invocation = Invocation(command="cmd", args=["--flag"], stdin="", env={})

    response = server.handle_invocation(invocation)

    assert response.stdout == "cmd", "Assertion failed"
    assert response.stderr == "", "Assertion failed"
    assert response.exit_code == 0, "Assertion failed"


def test_handle_invocation_custom_handler(tmp_path: Path) -> None:
    """Direct invocation handling should delegate to the configured handler."""
    seen: list[Invocation] = []

    def handler(invocation: Invocation) -> Response:
        seen.append(invocation)
        return Response(stdout="handled", stderr="err", exit_code=3)

    server = IPCServer(
        tmp_path / "ipc.sock",
        handlers=IPCHandlers(handler=handler),
    )
    invocation = Invocation(command="cmd", args=["--flag"], stdin="", env={})

    response = server.handle_invocation(invocation)

    assert [item.command for item in seen] == ["cmd"], "Assertion failed"
    assert response.stdout == "handled", "Assertion failed"
    assert response.stderr == "err", "Assertion failed"
    assert response.exit_code == 3, "Assertion failed"


def test_parse_payload_handles_invalid_utf8(caplog: pytest.LogCaptureFixture) -> None:
    """Invalid UTF-8 payloads should log and return ``None`` instead of raising."""
    caplog.set_level("ERROR", logger="cmd_mox.ipc.server")

    result = server._parse_payload(b"\xff\xfe")

    assert result is None, "Assertion failed"
    assert "malformed JSON" in caplog.text, "Assertion failed"


@pytest.mark.parametrize(
    (
        "kind",
        "payload",
        "expected_validator",
        "expected_processor",
    ),
    [
        (
            server.KIND_PASSTHROUGH_RESULT,
            {
                "invocation_id": "abc",
                "stdout": "out",
                "stderr": "err",
                "exit_code": 2,
            },
            server.validate_passthrough_payload,
            "handle_passthrough_result",
        ),
        (
            server.KIND_INVOCATION,
            {
                "command": "ls",
                "args": [],
                "stdin": "",
                "env": {},
            },
            server.validate_invocation_payload,
            "handle_invocation",
        ),
    ],
)
def test_parse_payload_returns_handler_metadata(
    kind: str,
    payload: _RequestPayload,
    expected_validator: server._RequestValidator,
    expected_processor: str,
) -> None:
    """Parsed requests should carry handler metadata for pipeline steps.

    The processor identifies the public server hook selected after validation.
    """
    raw = json.dumps({"kind": kind, **payload}).encode()

    parsed = server._parse_payload(raw)

    assert parsed is not None, "Assertion failed"
    assert parsed.kind == kind, "Assertion failed"
    assert parsed.validator is expected_validator, "Assertion failed"
    assert parsed.processor is expected_processor, "Assertion failed"
    assert parsed.payload == payload, "Assertion failed"


def test_request_pipeline_validates_and_dispatches(tmp_path: Path) -> None:
    """The request pipeline should validate, dispatch, and encode responses."""
    handled: list[str] = []

    def handler(invocation: Invocation) -> Response:
        handled.append(invocation.command)
        return Response(stdout="ok", stderr="", exit_code=0)

    ipc_server = IPCServer(
        tmp_path / "ipc.sock",
        handlers=IPCHandlers(handler=handler),
    )

    raw = json.dumps({
        "kind": server.KIND_INVOCATION,
        "command": "echo",
        "args": [],
        "stdin": "",
        "env": {},
    }).encode()

    response_bytes = server._request_pipeline(ipc_server, raw)

    assert response_bytes is not None, "Assertion failed"
    assert handled == ["echo"], "Assertion failed"
    payload = json.loads(response_bytes.decode("utf-8"))
    assert payload == {"stdout": "ok", "stderr": "", "exit_code": 0, "env": {}}, (
        "Assertion failed"
    )


@pytest.mark.parametrize(
    ("payload", "expected_stdout"),
    [
        (
            {
                "kind": server.KIND_INVOCATION,
                "command": "echo",
                "args": [],
                "stdin": "",
                "env": {},
            },
            "override:echo",
        ),
        (
            {
                "kind": server.KIND_PASSTHROUGH_RESULT,
                "invocation_id": "abc",
                "stdout": "",
                "stderr": "",
                "exit_code": 0,
            },
            "override:abc",
        ),
    ],
)
def test_request_pipeline_dispatches_to_overridden_handlers(
    tmp_path: Path,
    payload: _RequestPayload,
    expected_stdout: str,
) -> None:
    """Network request dispatch should honour overridden public handler hooks."""
    ipc_server = _OverridingIPCServer(tmp_path / "ipc.sock")

    response_bytes = server._request_pipeline(ipc_server, json.dumps(payload).encode())

    assert response_bytes is not None, "Request pipeline did not return a response"
    response = json.loads(response_bytes.decode("utf-8"))
    assert response["stdout"] == expected_stdout, "Override was bypassed by dispatch"


def test_socket_dispatches_to_overridden_invocation_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Socket requests should invoke an IPCServer invocation override."""
    socket_path = tmp_path / "ipc.sock"

    with _OverridingIPCServer(socket_path):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        response = invoke_server(
            Invocation(command="git", args=["status"], stdin="", env={}),
            timeout=1.0,
        )

    assert response.stdout == "override:git", "Invocation override was bypassed"


def test_socket_dispatches_to_overridden_passthrough_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Socket requests should invoke an IPCServer passthrough override."""
    socket_path = tmp_path / "ipc.sock"

    with _OverridingIPCServer(socket_path):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        response = report_passthrough_result(
            PassthroughResult(
                invocation_id="passthrough-123",
                stdout="",
                stderr="",
                exit_code=0,
            ),
            timeout=1.0,
        )

    assert response.stdout == "override:passthrough-123", (
        "Passthrough override was bypassed"
    )


def test_request_pipeline_logs_successful_invocation_dispatch(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Successful invocation dispatch should emit bounded metadata."""
    caplog.set_level("INFO", logger="cmd_mox.ipc.server")
    ipc_server = IPCServer(tmp_path / "ipc.sock")

    response = server._request_pipeline(
        ipc_server,
        json.dumps({
            "kind": server.KIND_INVOCATION,
            "command": "git",
            "args": ["status"],
            "stdin": "",
            "env": {},
        }).encode(),
    )

    assert response is not None, "Successful dispatch did not return a response"
    [event] = _dispatch_events(caplog)
    assert event["operation"] == "ipc.dispatch", "Wrong operation"
    assert event["kind"] == server.KIND_INVOCATION, "Wrong request kind"
    assert event["outcome"] == "success", "Wrong dispatch outcome"
    assert "invocation_id" not in event, "Invocation ID must be omitted"
    assert "error_category" not in event, "Success must not include an error"


def test_request_pipeline_logs_successful_passthrough_dispatch(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    echo_handler: cabc.Callable[[Invocation], Response],
    passthrough_handler: cabc.Callable[[PassthroughResult], Response],
) -> None:
    """Successful passthrough dispatch should include its invocation ID."""
    caplog.set_level("INFO", logger="cmd_mox.ipc.server")
    ipc_server = IPCServer(
        tmp_path / "ipc.sock",
        handlers=IPCHandlers(
            handler=echo_handler,
            passthrough_handler=passthrough_handler,
        ),
    )

    response = server._request_pipeline(
        ipc_server,
        json.dumps({
            "kind": server.KIND_PASSTHROUGH_RESULT,
            "invocation_id": "passthrough-123",
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
        }).encode(),
    )

    assert response is not None, "Successful dispatch did not return a response"
    [event] = _dispatch_events(caplog)
    assert event["kind"] == server.KIND_PASSTHROUGH_RESULT, "Wrong kind"
    assert event["invocation_id"] == "passthrough-123", "Wrong ID"
    assert event["outcome"] == "success", "Wrong dispatch outcome"
    assert "error_category" not in event, "Success must not include an error"


def test_request_pipeline_logs_invalid_request(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Invalid requests should emit a bounded validation outcome."""
    caplog.set_level("INFO", logger="cmd_mox.ipc.server")
    ipc_server = IPCServer(tmp_path / "ipc.sock")

    response = server._request_pipeline(
        ipc_server,
        json.dumps({"kind": server.KIND_INVOCATION}).encode(),
    )

    assert response is None, "Invalid request unexpectedly produced a response"
    [event] = _dispatch_events(caplog)
    assert event["kind"] == server.KIND_INVOCATION, "Wrong request kind"
    assert event["outcome"] == "invalid_request", "Wrong outcome"
    assert event["error_category"] == "ValidationError", "Wrong error"
    assert "invocation_id" not in event, "Invocation ID must be omitted"


def test_request_pipeline_logs_handler_failure(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Handler failures should emit only a bounded error category."""
    caplog.set_level("INFO", logger="cmd_mox.ipc.server")

    def failing_handler(_invocation: Invocation) -> Response:
        msg = "handler failure"
        raise RuntimeError(msg)

    ipc_server = IPCServer(
        tmp_path / "ipc.sock",
        handlers=IPCHandlers(handler=failing_handler),
    )
    response = server._request_pipeline(
        ipc_server,
        json.dumps({
            "kind": server.KIND_INVOCATION,
            "command": "git",
            "args": [],
            "stdin": "",
            "env": {},
        }).encode(),
    )

    assert response is not None, "Handler failure did not return an error response"
    [event] = _dispatch_events(caplog)
    assert event["kind"] == server.KIND_INVOCATION, "Wrong request kind"
    assert event["outcome"] == "handler_error", "Wrong outcome"
    assert event["error_category"] == "RuntimeError", "Wrong error"
    assert "invocation_id" not in event, "Invocation ID must be omitted"


def test_request_pipeline_validation_failure_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Validation failures should short-circuit dispatch."""
    calls: list[Invocation] = []

    def failing_validator(_payload: _RequestPayload) -> Invocation | None:
        return None

    def spy_processor(
        _server: IPCServer, invocation: Invocation
    ) -> Response:  # pragma: no cover - guarded by validation
        calls.append(invocation)
        return Response(stdout="ok")

    monkeypatch.setitem(
        server._REQUEST_HANDLERS,
        server.KIND_INVOCATION,
        (failing_validator, spy_processor),
    )

    ipc_server = IPCServer(tmp_path / "ipc.sock")
    raw = json.dumps({
        "kind": server.KIND_INVOCATION,
        "command": "echo",
        "args": [],
        "stdin": "",
        "env": {},
    }).encode()

    response_bytes = server._request_pipeline(ipc_server, raw)

    assert response_bytes is None, "Assertion failed"
    assert calls == [], "Assertion failed"


def test_decode_payload_rejects_non_mapping(caplog: pytest.LogCaptureFixture) -> None:
    """Non-object JSON should be rejected with a clear log entry."""
    caplog.set_level("ERROR", logger="cmd_mox.ipc.server")

    result = server._decode_payload(json.dumps([1, 2, 3]).encode())

    assert result is None, "Assertion failed"
    assert "IPC payload is not a mapping" in caplog.text, "Assertion failed"


def test_encode_response_serialises_response_fields(
    snapshot: SnapshotAssertion,
) -> None:
    """Response encoding should produce JSON bytes matching the model."""
    response = Response(stdout="out", stderr="err", exit_code=5, env={"KEY": "VAL"})

    encoded = server._encode_response(response)
    payload = json.loads(encoded.decode("utf-8"))

    assert payload == snapshot, "Encoded IPC response payload changed"


def test_request_pipeline_rejects_unknown_kind(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Unknown IPC kinds should be logged and ignored without dispatch."""
    caplog.set_level("ERROR", logger="cmd_mox.ipc.server")
    ipc_server = IPCServer(tmp_path / "ipc.sock")

    response = server._request_pipeline(
        ipc_server,
        json.dumps({"kind": "mystery"}).encode(),
    )

    assert response is None, "Assertion failed"
    assert "Unknown IPC payload kind" in caplog.text, "Assertion failed"


def test_ipcserver_stop_is_thread_safe(tmp_path: Path) -> None:
    """Stopping the server concurrently should not raise race conditions."""
    server = IPCServer(tmp_path / "ipc.sock")
    server.start()

    try:
        threads = [threading.Thread(target=server.stop) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        # Additional stop should be a no-op when the server is already stopped.
        server.stop()


def test_handle_passthrough_default(tmp_path: Path) -> None:
    """Direct passthrough handling should raise when no handler is set."""
    server = IPCServer(tmp_path / "ipc.sock")
    result = PassthroughResult(
        invocation_id="123",
        stdout="out",
        stderr="err",
        exit_code=0,
    )

    with pytest.raises(RuntimeError, match="Unhandled passthrough result"):
        server.handle_passthrough_result(result)


def test_handle_passthrough_handler_exception(tmp_path: Path) -> None:
    """Passthrough handler exceptions should be wrapped for callers."""

    def failing_handler(_result: PassthroughResult) -> Response:
        raise ValueError("boom")

    server = IPCServer(
        tmp_path / "ipc.sock",
        handlers=IPCHandlers(passthrough_handler=failing_handler),
    )
    result = PassthroughResult(
        invocation_id="123",
        stdout="out",
        stderr="err",
        exit_code=0,
    )

    with pytest.raises(
        RuntimeError,
        match="Exception in passthrough handler for 123: boom",
    ) as excinfo:
        server.handle_passthrough_result(result)

    assert isinstance(excinfo.value.__cause__, ValueError), "Assertion failed"


@pytest.mark.parametrize(
    "test_case",
    [
        pytest.param(
            TimeoutTestCase(
                timeouts_arg=TimeoutConfig(timeout=1.25, accept_timeout=0.2),
                expected_timeout=1.25,
                expected_accept_timeout=0.2,
            ),
            id="custom_timeouts",
        ),
        pytest.param(
            TimeoutTestCase(
                timeouts_arg=None,
                expected_timeout=TimeoutConfig().timeout,
                expected_accept_timeout=min(0.1, TimeoutConfig().timeout / 10),
            ),
            id="default_timeouts",
        ),
    ],
)
def test_callback_ipcserver_timeout_config(
    tmp_path: Path,
    echo_handler: cabc.Callable[[Invocation], Response],
    passthrough_handler: cabc.Callable[[PassthroughResult], Response],
    test_case: TimeoutTestCase,
) -> None:
    """CallbackIPCServer should handle TimeoutConfig correctly."""
    server = CallbackIPCServer(
        tmp_path / "ipc.sock",
        echo_handler,
        passthrough_handler,
        timeouts=test_case.timeouts_arg,
    )

    assert server.timeout == test_case.expected_timeout, "Assertion failed"
    assert server.accept_timeout == test_case.expected_accept_timeout, (
        "Assertion failed"
    )


def test_timeout_config_validation() -> None:
    """TimeoutConfig should reject non-positive timeout values."""
    with pytest.raises(ValueError, match="timeout must be > 0 and finite"):
        TimeoutConfig(timeout=0.0)

    with pytest.raises(ValueError, match="accept_timeout must be > 0 and finite"):
        TimeoutConfig(accept_timeout=0.0)
