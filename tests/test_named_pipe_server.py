"""Windows-specific NamedPipeServer integration tests."""

from __future__ import annotations

import os
import pathlib

import pytest

from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    EnvironmentManager,
)
from cmd_mox.ipc import Invocation, NamedPipeServer, invoke_server

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Named pipes require Windows")


def test_named_pipe_server_roundtrip(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NamedPipeServer should echo the command name without handlers."""
    socket_path = pathlib.Path(tmp_path) / "ipc.sock"
    with NamedPipeServer(socket_path, timeout=1.0):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="whoami", args=[], stdin="", env={})
        response = invoke_server(invocation, timeout=1.0)
        assert response.stdout == "whoami"


def test_named_pipe_server_exports_environment() -> None:
    """Starting NamedPipeServer under EnvironmentManager publishes env vars."""
    original_socket = os.environ.get(CMOX_IPC_SOCKET_ENV)
    original_timeout = os.environ.get(CMOX_IPC_TIMEOUT_ENV)

    with EnvironmentManager() as env:
        assert env.socket_path is not None
        with NamedPipeServer(env.socket_path, timeout=1.5):
            assert os.environ[CMOX_IPC_SOCKET_ENV] == str(env.socket_path)
            assert os.environ[CMOX_IPC_TIMEOUT_ENV] == "1.5"

    assert os.environ.get(CMOX_IPC_SOCKET_ENV) == original_socket
    assert os.environ.get(CMOX_IPC_TIMEOUT_ENV) == original_timeout
