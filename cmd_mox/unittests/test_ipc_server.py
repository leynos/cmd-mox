"""Unit tests for the IPC server component."""

import io
import logging
import os
import socket
import threading
import time
import types
import typing as typ
from pathlib import Path

import pytest

from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    EnvironmentManager,
)
from cmd_mox.ipc import (
    DEFAULT_CONNECT_JITTER,
    Invocation,
    IPCServer,
    RetryConfig,
    invoke_server,
)
from cmd_mox.ipc import server as ipc_server_module
from cmd_mox.ipc.server import _IPCHandler

pytestmark = pytest.mark.requires_unix_sockets


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


def test_ipc_server_exports_environment() -> None:
    """Starting IPCServer under EnvironmentManager publishes env vars."""
    original_socket = os.environ.get(CMOX_IPC_SOCKET_ENV)
    original_timeout = os.environ.get(CMOX_IPC_TIMEOUT_ENV)

    with EnvironmentManager() as env:
        assert env.socket_path is not None
        with IPCServer(env.socket_path, timeout=1.25):
            assert os.environ[CMOX_IPC_SOCKET_ENV] == str(env.socket_path)
            assert os.environ[CMOX_IPC_TIMEOUT_ENV] == "1.25"

    assert os.environ.get(CMOX_IPC_SOCKET_ENV) == original_socket
    assert os.environ.get(CMOX_IPC_TIMEOUT_ENV) == original_timeout


def test_ipc_server_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Server echoes command name via IPC."""
    socket_path = tmp_path / "ipc.sock"
    with IPCServer(socket_path):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="ls", args=["-l"], stdin="", env={})
        response = invoke_server(invocation, timeout=2.0)
        assert response.stdout == "ls"


def test_ipc_server_readiness_probe_is_a_closed_connection(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Readiness probes close cleanly without being logged as malformed JSON."""
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc._server_core")

    with IPCServer(tmp_path / "ipc.sock"):
        deadline = time.monotonic() + 1.0
        while "connection closed" not in caplog.text and time.monotonic() < deadline:
            time.sleep(0.01)

    assert "connection closed" in caplog.text, "Readiness probe closure was not named"
    assert "malformed JSON" not in caplog.text, "Readiness probe was called malformed"


def test_ipc_server_bounds_incomplete_request_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A client that sends nothing times out and does not block later requests."""
    socket_path = tmp_path / "ipc.sock"
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc.server")

    with IPCServer(socket_path, timeout=0.1):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as incomplete:
            incomplete.settimeout(1.0)
            incomplete.connect(str(socket_path))
            deadline = time.monotonic() + 1.0
            while (
                "timed out while receiving request" not in caplog.text
                and time.monotonic() < deadline
            ):
                time.sleep(0.01)

        assert "timed out while receiving request" in caplog.text, (
            "Incomplete request did not hit its per-connection read timeout"
        )
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        response = invoke_server(
            Invocation(command="after-timeout", args=[], stdin="", env={}),
            timeout=1.0,
        )

    assert response.stdout == "after-timeout", "Timed-out client stalled the server"


@pytest.mark.parametrize(
    ("stage", "failure_type"),
    [
        ("write", BrokenPipeError),
        ("flush", ConnectionResetError),
        ("flush", OSError),
    ],
)
def test_ipc_handler_logs_response_connection_drop(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    stage: str,
    failure_type: type[OSError],
) -> None:
    """Response write and flush failures become bounded connection-drop logs."""

    class BrokenWriter:
        def write(self, _text: str) -> int:
            if stage == "write":
                raise failure_type
            return 1

        def flush(self) -> None:
            if stage == "flush":
                raise failure_type

    fake_handler = typ.cast(
        "_IPCHandler",
        types.SimpleNamespace(
            rfile=io.BytesIO(b"request"),
            server=types.SimpleNamespace(outer=object()),
            wfile=BrokenWriter(),
        ),
    )
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc.server")
    monkeypatch.setattr(ipc_server_module, "_request_pipeline", lambda *_args: b"reply")

    _IPCHandler.handle(fake_handler)

    assert "connection closed" in caplog.text, (
        "Dropped response connection was not logged"
    )


def test_ipc_server_start_fails_if_in_use(tmp_path: Path) -> None:
    """Starting a second server on the same socket raises RuntimeError."""
    socket_path = tmp_path / "ipc.sock"
    with IPCServer(socket_path):
        other = IPCServer(socket_path)
        with pytest.raises(RuntimeError, match="in use"):
            other.start()


@pytest.mark.parametrize(
    ("jitter", "_description"),
    [
        (None, "default jitter"),
        (0.5, "custom jitter=0.5"),
    ],
)
def test_invoke_server_retries_connection_parametrized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    jitter: float | None,
    _description: str,
) -> None:
    """Client retries until the server becomes available."""
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
        if jitter is None:
            retry_config = RetryConfig(retries=5, backoff=0.01)
        else:
            retry_config = RetryConfig(retries=5, backoff=0.01, jitter=jitter)
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
    kwargs: dict[str, typ.Any],
    match: str,
) -> None:
    """Client rejects invalid retry configuration."""
    socket_path = tmp_path / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="ls", args=[], stdin="", env={})
    timeout = kwargs.get("timeout", 1.0)
    retries = kwargs.get("retries", 1)
    backoff = kwargs.get("backoff", 0.0)
    jitter = kwargs.get("jitter", DEFAULT_CONNECT_JITTER)
    with pytest.raises(ValueError, match=match):
        invoke_server(
            invocation,
            timeout=timeout,
            retry_config=RetryConfig(
                retries=retries,
                backoff=backoff,
                jitter=jitter,
            ),
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
