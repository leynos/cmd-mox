"""Unit tests for the IPC server component."""

import socket
import threading
import typing as t
from pathlib import Path
from unittest.mock import patch

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import (
    DEFAULT_CONNECT_JITTER,
    Invocation,
    IPCServer,
    RetryConfig,
    _calculate_retry_delay,
    invoke_server,
)


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


def test_ipc_server_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Server echoes command name via IPC."""
    socket_path = tmp_path / "ipc.sock"
    with IPCServer(socket_path):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="ls", args=["-l"], stdin="", env={})
        response = invoke_server(invocation, timeout=2.0)
        assert response.stdout == "ls"


def test_ipc_server_start_fails_if_in_use(tmp_path: Path) -> None:
    """Starting a second server on the same socket raises RuntimeError."""
    socket_path = tmp_path / "ipc.sock"
    with IPCServer(socket_path):
        other = IPCServer(socket_path)
        with pytest.raises(RuntimeError, match="in use"):
            other.start()


def test_invoke_server_retries_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Client retries connecting until the server becomes available."""
    socket_path = tmp_path / "ipc.sock"

    server = IPCServer(socket_path)

    def delayed_start() -> None:
        import time

        time.sleep(0.05)
        server.start()

    thread = threading.Thread(target=delayed_start)
    thread.start()
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="ls", args=[], stdin="", env={})
    try:
        retry_config = RetryConfig(retries=5, backoff=0.01)
        response = invoke_server(invocation, timeout=1.0, retry_config=retry_config)
        assert response.stdout == "ls"
    finally:
        thread.join()
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
    kwargs: dict[str, t.Any],
    match: str,
) -> None:
    """Client rejects invalid retry configuration."""
    socket_path = tmp_path / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="ls", args=[], stdin="", env={})
    timeout = t.cast("float", kwargs.get("timeout", 1.0))
    retry_kwargs = {
        "retries": t.cast("int", kwargs.get("retries", 1)),
        "backoff": t.cast("float", kwargs.get("backoff", 0.0)),
        "jitter": t.cast("float", kwargs.get("jitter", DEFAULT_CONNECT_JITTER)),
    }
    with pytest.raises(ValueError, match=match):
        invoke_server(
            invocation,
            timeout=timeout,
            retry_config=RetryConfig(**retry_kwargs),
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


def test_calculate_retry_delay() -> None:
    """Retry delay scales linearly and applies jitter bounds."""
    assert _calculate_retry_delay(1, 0.1, 0.0) == pytest.approx(0.2)
    with patch("cmd_mox.ipc.random.uniform", return_value=1.25) as mock_uniform:
        delay = _calculate_retry_delay(0, 1.0, 0.5)
        assert delay == pytest.approx(1.25)
        mock_uniform.assert_called_once_with(0.5, 1.5)
