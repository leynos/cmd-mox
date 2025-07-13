"""Unit tests for the IPC server component."""

from pathlib import Path

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import Invocation, IPCServer, invoke_server


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
