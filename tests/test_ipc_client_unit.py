"""Unit tests for IPC client helpers."""

from __future__ import annotations

import pathlib
import socket
import typing as typ

import pytest

from cmd_mox.ipc import client as ipc_client
from cmd_mox.ipc.client import (
    RetryConfig,
    _connect_unix_with_retries,
    _ConnectionContext,
    invoke_server,
    report_passthrough_result,
)
from cmd_mox.ipc.constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from cmd_mox.ipc.models import Invocation, PassthroughResult, Response


class _FakeSocket:
    """Socket double that toggles between failure and success."""

    attempts: int = 0
    succeed_after: int = 1

    def __init__(self, *_: object, **__: object) -> None:
        self.closed = False

    def settimeout(self, _timeout: float) -> None:
        pass

    def connect(self, _address: str) -> None:
        type(self).attempts += 1
        if type(self).attempts < type(self).succeed_after:
            raise ConnectionRefusedError

    def close(self) -> None:
        self.closed = True


class _AlwaysFailSocket(_FakeSocket):
    succeed_after = 10


@pytest.fixture(autouse=True)
def _reset_fake_sockets() -> None:
    """Reset socket counters between tests."""
    _FakeSocket.attempts = 0
    _FakeSocket.succeed_after = 1
    _AlwaysFailSocket.attempts = 0
    _AlwaysFailSocket.succeed_after = 10


def test_retry_config_validates_inputs() -> None:
    """RetryConfig should reject invalid values eagerly."""
    with pytest.raises(ValueError, match="retries must be >= 1"):
        RetryConfig(retries=0)
    with pytest.raises(ValueError, match="backoff must be >= 0 and finite"):
        RetryConfig(backoff=-1)
    with pytest.raises(ValueError, match="jitter must be between 0 and 1"):
        RetryConfig(jitter=2.0)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(1.5, id="float"),
        pytest.param("3", id="str"),
        pytest.param(None, id="none"),
        pytest.param(True, id="bool"),
    ],
)
def test_retry_config_rejects_non_integer_retries(value: object) -> None:
    """RetryConfig should reject non-integer retry counts eagerly."""
    with pytest.raises(TypeError, match=r"^retries must be an integer$"):
        RetryConfig(retries=value)  # type: ignore[arg-type, ty:invalid-argument-type] - the test deliberately supplies non-integer retry counts


def test_retry_config_validate_checks_timeout() -> None:
    """RetryConfig.validate should validate timeout in addition to fields."""
    config = RetryConfig()
    with pytest.raises(ValueError, match="timeout must be > 0 and finite"):
        config.validate(0.0)


def test_connect_unix_with_retries_eventually_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """_connect_unix_with_retries should retry until the socket connects."""
    tmp_path = pathlib.Path(tmp_path)
    _FakeSocket.succeed_after = 2
    monkeypatch.setattr(socket, "socket", _FakeSocket)

    retry_config = RetryConfig(retries=3, backoff=0.0, jitter=0.0)
    sock = _connect_unix_with_retries(
        tmp_path / "ipc.sock",
        _ConnectionContext(timeout=0.1, retry_config=retry_config),
    )

    assert isinstance(sock, _FakeSocket), "Assertion failed"
    assert _FakeSocket.attempts == 2, "Assertion failed"


def test_connect_unix_with_retries_raises_after_exhaustion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """_connect_unix_with_retries should raise the final OSError."""
    tmp_path = pathlib.Path(tmp_path)
    monkeypatch.setattr(socket, "socket", _AlwaysFailSocket)
    retry_config = RetryConfig(retries=2, backoff=0.0, jitter=0.0)

    with pytest.raises(ConnectionRefusedError, match=r"^$"):
        _connect_unix_with_retries(
            tmp_path / "ipc.sock",
            _ConnectionContext(timeout=0.1, retry_config=retry_config),
        )
    assert _AlwaysFailSocket.attempts == 2, "Assertion failed"


def test_invoke_server_uses_named_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """invoke_server should send invocation requests with the expected kind."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    captured: dict[str, typ.Any] = {}

    def fake_send(
        kind: str,
        data: dict[str, typ.Any],
        options: ipc_client._RequestOptions,
    ) -> Response:
        captured["kind"] = kind
        captured["data"] = data
        assert options.retry_config is None, "Assertion failed"
        return Response(stdout="ok")

    monkeypatch.setattr("cmd_mox.ipc.client._send_request", fake_send)

    response = invoke_server(invocation, timeout=1.0, retry_config=None)

    assert response.stdout == "ok", "Assertion failed"
    assert captured["kind"] == KIND_INVOCATION, "Assertion failed"
    assert captured["data"] == invocation.to_dict(), "Assertion failed"


def test_invoke_server_forwards_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absolute deadline reaches the shared request context."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    deadline = 123.5
    captured: dict[str, object] = {}

    def fake_send(
        kind: str,
        data: dict[str, typ.Any],
        options: ipc_client._RequestOptions,
    ) -> Response:
        captured["kind"] = kind
        captured["data"] = data
        captured["timeout"] = options.timeout
        captured["retry"] = options.retry_config
        captured["deadline"] = options.deadline
        return Response(stdout="ok")

    monkeypatch.setattr(ipc_client, "_send_request", fake_send)

    invoke_server(invocation, timeout=1.0, deadline=deadline)

    assert captured["deadline"] == deadline, "Invocation deadline was not forwarded"


def test_invoke_server_does_not_restart_expired_encoding_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Payload encoding cannot restart a shim's absolute IPC deadline."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    now = {"value": 100.0}

    def encode_after_deadline(
        _kind: str, _data: dict[str, typ.Any]
    ) -> tuple[bytes, str]:
        now["value"] = 102.0
        return b"{}", "correlation"

    def unexpected_socket(*_args: object, **_kwargs: object) -> typ.NoReturn:
        return pytest.fail("Socket creation must not follow an expired deadline")

    monkeypatch.setattr(ipc_client, "_build_request_envelope", encode_after_deadline)
    monkeypatch.setattr(
        ipc_client, "_get_validated_socket_path", lambda: tmp_path / "ipc.sock"
    )
    monkeypatch.setattr(ipc_client.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(ipc_client.path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(ipc_client.socket, "socket", unexpected_socket)

    with pytest.raises(TimeoutError):
        invoke_server(invocation, timeout=1.0, deadline=101.0)


def test_report_passthrough_result_uses_named_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """report_passthrough_result should send responses with the expected kind."""
    result = PassthroughResult(invocation_id="1", stdout="", stderr="", exit_code=0)
    captured: dict[str, typ.Any] = {}

    def fake_send(
        kind: str,
        data: dict[str, typ.Any],
        options: ipc_client._RequestOptions,
    ) -> Response:
        captured["kind"] = kind
        captured["data"] = data
        return Response(stdout="ok")

    monkeypatch.setattr("cmd_mox.ipc.client._send_request", fake_send)

    report_passthrough_result(result, timeout=1.0)

    assert captured["kind"] == KIND_PASSTHROUGH_RESULT, "Assertion failed"
    assert captured["data"] == result.to_dict(), "Assertion failed"


def test_report_passthrough_result_forwards_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passthrough reports can share their caller's absolute deadline."""
    result = PassthroughResult(invocation_id="1", stdout="", stderr="", exit_code=0)
    deadline = 123.5
    captured: dict[str, object] = {}

    def fake_send(
        kind: str,
        data: dict[str, typ.Any],
        options: ipc_client._RequestOptions,
    ) -> Response:
        captured["deadline"] = options.deadline
        return Response(stdout="ok")

    monkeypatch.setattr(ipc_client, "_send_request", fake_send)

    report_passthrough_result(result, timeout=1.0, deadline=deadline)

    assert captured["deadline"] == deadline, "Passthrough deadline was not forwarded"
