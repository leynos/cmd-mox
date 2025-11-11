"""Windows-specific tests for the NamedPipeServer implementation."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import (
    CallbackIPCServer,
    Invocation,
    NamedPipeServer,
    PassthroughResult,
    Response,
    invoke_server,
)

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="NamedPipeServer only runs on Windows"
)


def _dummy_passthrough(result: PassthroughResult) -> Response:
    """Return a Response mirroring passthrough data for CallbackIPCServer tests."""
    return Response(
        stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code
    )


def test_named_pipe_server_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    """NamedPipeServer should accept shim invocations via invoke_server."""
    pipe_path = Path(rf"\\.\pipe\cmdmox-test-{uuid.uuid4().hex}")
    server = NamedPipeServer(pipe_path)
    with server:
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(pipe_path))
        invocation = Invocation(command="whoami", args=[], stdin="", env={})
        response = invoke_server(invocation, timeout=1.0)
        assert response.stdout == "whoami"
        assert response.stderr == ""
        assert response.exit_code == 0


def test_callback_ipc_server_prefers_named_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CallbackIPCServer should wrap a NamedPipeServer when given a pipe path."""
    pipe_path = Path(rf"\\.\pipe\cmdmox-test-{uuid.uuid4().hex}")

    def handler(invocation: Invocation) -> Response:
        return Response(stdout=f"handled {invocation.command}")

    server = CallbackIPCServer(
        pipe_path,
        handler,
        _dummy_passthrough,
    )
    assert isinstance(server, NamedPipeServer)
    with server:
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(pipe_path))
        invocation = Invocation(command="cmd-mock", args=[], stdin="", env={})
        response = invoke_server(invocation, timeout=1.0)
        assert response.stdout == "handled cmd-mock"
