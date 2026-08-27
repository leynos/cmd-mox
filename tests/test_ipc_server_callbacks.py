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
        """Return a response that identifies the overridden invocation hook.

        Returns
        -------
        Response
            A response whose stdout names the overridden invocation's command.
        """
        return Response(stdout=f"override:{invocation.command}")

    def handle_passthrough_result(self, result: PassthroughResult) -> Response:
        """Return a response that identifies the overridden passthrough hook.

        Returns
        -------
        Response
            A response whose stdout names the overridden passthrough's invocation id.
        """
        return Response(stdout=f"override:{result.invocation_id}")


@dc.dataclass(slots=True)
class TimeoutTestCase:
    """Test case configuration for timeout validation."""

    timeouts_arg: TimeoutConfig | None
    expected_timeout: float
    expected_accept_timeout: float


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
        """Record invocations and return a distinctive response.

        Returns
        -------
        Response
            A fixed response identifying this handler as having run.
        """
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
        """Capture passthrough results and return a custom response.

        Returns
        -------
        Response
            A fixed response identifying this passthrough handler as having run.
        """
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

    assert result is None, "Non-object JSON unexpectedly produced a payload mapping"
    assert "IPC payload is not a mapping" in caplog.text, (
        "Non-mapping JSON was not logged without including its payload contents"
    )


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

    # Exceptions raised inside a thread target are printed to stderr and never
    # reach the joining thread, so they must be captured explicitly for the
    # assertions below to have any force.
    failures: list[BaseException] = []

    def stop_and_capture() -> None:
        try:
            server.stop()
        except BaseException as exc:  # ruff: ignore[blind-except] - thread targets must not propagate; the failure is surfaced by the assertion below
            failures.append(exc)

    try:
        threads = [threading.Thread(target=stop_and_capture) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        # Additional stop should be a no-op when the server is already stopped.
        server.stop()

    assert not failures, f"concurrent stop() raised: {failures}"
    assert server._server is None, "Assertion failed"
    assert server._thread is None, "Assertion failed"


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
