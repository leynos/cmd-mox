"""Unit tests for IPC client helpers."""

from __future__ import annotations

import pathlib
import socket
import typing as t

import pytest

from cmd_mox.ipc.client import (
    RetryConfig,
    _connect_unix_with_retries,
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
    monkeypatch.setattr(socket, "socket", _FakeSocket)  # type: ignore[assignment]

    retry_config = RetryConfig(retries=3, backoff=0.0, jitter=0.0)
    sock = _connect_unix_with_retries(
        tmp_path / "ipc.sock", timeout=0.1, retry_config=retry_config
    )

    assert isinstance(sock, _FakeSocket)
    assert _FakeSocket.attempts == 2


def test_connect_unix_with_retries_raises_after_exhaustion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """_connect_unix_with_retries should raise the final OSError."""
    tmp_path = pathlib.Path(tmp_path)
    monkeypatch.setattr(socket, "socket", _AlwaysFailSocket)  # type: ignore[assignment]
    retry_config = RetryConfig(retries=2, backoff=0.0, jitter=0.0)

    with pytest.raises(ConnectionRefusedError, match=r"^$"):
        _connect_unix_with_retries(
            tmp_path / "ipc.sock", timeout=0.1, retry_config=retry_config
        )
    assert _AlwaysFailSocket.attempts == 2


def test_invoke_server_uses_named_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """invoke_server should send invocation requests with the expected kind."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    captured: dict[str, t.Any] = {}

    def fake_send(
        kind: str, data: dict[str, t.Any], timeout: float, retry: RetryConfig | None
    ) -> Response:
        captured["kind"] = kind
        captured["data"] = data
        assert retry is None
        return Response(stdout="ok")

    monkeypatch.setattr("cmd_mox.ipc.client._send_request", fake_send)

    response = invoke_server(invocation, timeout=1.0, retry_config=None)

    assert response.stdout == "ok"
    assert captured["kind"] == KIND_INVOCATION
    assert captured["data"] == invocation.to_dict()


def test_report_passthrough_result_uses_named_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """report_passthrough_result should send responses with the expected kind."""
    result = PassthroughResult(invocation_id="1", stdout="", stderr="", exit_code=0)
    captured: dict[str, t.Any] = {}

    def fake_send(
        kind: str, data: dict[str, t.Any], timeout: float, retry: RetryConfig | None
    ) -> Response:
        captured["kind"] = kind
        captured["data"] = data
        return Response(stdout="ok")

    monkeypatch.setattr("cmd_mox.ipc.client._send_request", fake_send)

    report_passthrough_result(result, timeout=1.0)

    assert captured["kind"] == KIND_PASSTHROUGH_RESULT
    assert captured["data"] == result.to_dict()
