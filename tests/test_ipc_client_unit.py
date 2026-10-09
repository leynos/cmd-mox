"""Unit tests for IPC client helpers."""

from __future__ import annotations

import os
import pathlib
import socket
import threading
import time
import typing as typ

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
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
        context: _ConnectionContext,
    ) -> Response:
        captured["kind"] = kind
        captured["data"] = data
        assert context.retry_config.retries == 3, "Default retry config was not used"
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
        context: _ConnectionContext,
    ) -> Response:
        captured["kind"] = kind
        captured["data"] = data
        captured["timeout"] = context.timeout
        captured["retry"] = context.retry_config
        captured["deadline"] = context.deadline
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


@pytest.mark.skipif(os.name == "nt", reason="Unix socket deadline behaviour")
def test_invoke_server_bounds_fragmented_response_to_one_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Slow response chunks cannot renew the Unix client's I/O budget."""
    socket_path = tmp_path / "delayed-response.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    accepted = threading.Event()
    release = threading.Event()

    def serve() -> None:
        try:
            connection, _address = listener.accept()
            with connection:
                while connection.recv(64 * 1024):
                    pass
                accepted.set()
                for chunk in (
                    b'{"stdout":',
                    b'"slow"',
                    b',"stderr":""',
                    b',"exit_code":0,"env":{}}',
                ):
                    if release.wait(0.05):
                        return
                    connection.sendall(chunk)
        except OSError:
            # The client closes its side when its shared deadline expires.
            pass

    server_thread = threading.Thread(target=serve, daemon=True)
    server_thread.start()
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    started = time.monotonic()

    try:
        with pytest.raises(TimeoutError):
            invoke_server(
                invocation,
                timeout=0.12,
                retry_config=RetryConfig(retries=1, backoff=0.0, jitter=0.0),
            )

        elapsed = time.monotonic() - started
        assert accepted.wait(1.0), "Unix peer did not receive the request"
        assert elapsed < 0.8, "Client response read exceeded its single deadline"
    finally:
        release.set()
        listener.close()
        server_thread.join(timeout=1.0)

    assert not server_thread.is_alive(), "Unix response peer did not stop"


@pytest.mark.skipif(os.name == "nt", reason="Unix socket retry behaviour")
def test_unix_connect_retry_sleep_is_capped_by_the_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Retry backoff stays within one deadline and skips later attempts."""
    clock = {"value": 10.0}
    created: list[float] = []
    connected: list[float] = []
    timeouts: list[float] = []
    sleeps: list[float] = []

    class _ClockedSocket:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            created.append(clock["value"])

        def settimeout(self, timeout: float) -> None:
            timeouts.append(timeout)

        def connect(self, _address: str) -> None:
            connected.append(clock["value"])
            raise ConnectionRefusedError

        def close(self) -> None:
            pass

    def advance_clock(delay: float) -> None:
        sleeps.append(delay)
        clock["value"] += delay

    monkeypatch.setattr(ipc_client.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(ipc_client.time, "sleep", advance_clock)
    monkeypatch.setattr(ipc_client.socket, "socket", _ClockedSocket)
    context = _ConnectionContext(
        timeout=0.05,
        retry_config=RetryConfig(retries=5, backoff=0.04, jitter=0.0),
    )

    with pytest.raises(TimeoutError):
        _connect_unix_with_retries(tmp_path / "missing.sock", context)

    assert created == pytest.approx([10.0, 10.04]), (
        "A new socket attempt started after the deadline"
    )
    assert connected == pytest.approx([10.0, 10.04]), (
        "A connection attempt started after the deadline"
    )
    assert timeouts == pytest.approx([0.05, 0.01]), (
        "Connect timeouts did not use the remaining deadline"
    )
    assert sleeps == pytest.approx([0.04, 0.01]), (
        "Retry sleeps were not capped to the remaining deadline"
    )


def test_report_passthrough_result_uses_named_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """report_passthrough_result should send responses with the expected kind."""
    result = PassthroughResult(invocation_id="1", stdout="", stderr="", exit_code=0)
    captured: dict[str, typ.Any] = {}

    def fake_send(
        kind: str,
        data: dict[str, typ.Any],
        context: _ConnectionContext,
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
        context: _ConnectionContext,
    ) -> Response:
        captured["deadline"] = context.deadline
        return Response(stdout="ok")

    monkeypatch.setattr(ipc_client, "_send_request", fake_send)

    report_passthrough_result(result, timeout=1.0, deadline=deadline)

    assert captured["deadline"] == deadline, "Passthrough deadline was not forwarded"
