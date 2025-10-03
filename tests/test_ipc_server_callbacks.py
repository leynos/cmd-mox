"""Tests covering :class:`cmd_mox.ipc.IPCServer` callback behaviour."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import (
    Invocation,
    IPCServer,
    PassthroughResult,
    Response,
    invoke_server,
    report_passthrough_result,
)

if t.TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.usefixtures("tmp_path")
def test_ipcserver_invocation_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IPCServer should delegate invocations to the configured handler."""
    socket_path = tmp_path / "ipc.sock"
    seen: list[Invocation] = []

    def handler(invocation: Invocation) -> Response:
        """Record invocations and return a distinctive response."""
        seen.append(invocation)
        return Response(stdout="handled", stderr="err", exit_code=2)

    with IPCServer(socket_path, handler=handler):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="cmd", args=["--flag"], stdin="", env={})
        response = invoke_server(invocation, timeout=1.0)

    assert seen
    assert seen[0].command == "cmd"
    assert response.stdout == "handled"
    assert response.stderr == "err"
    assert response.exit_code == 2


@pytest.mark.usefixtures("tmp_path")
def test_ipcserver_passthrough_handler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IPCServer should delegate passthrough results when a handler is provided."""
    socket_path = tmp_path / "ipc.sock"
    seen: list[PassthroughResult] = []

    def handler(invocation: Invocation) -> Response:
        """Return a simple echo to keep the invocation path exercised."""
        return Response(stdout=invocation.command)

    def passthrough_handler(result: PassthroughResult) -> Response:
        """Capture passthrough results and return a custom response."""
        seen.append(result)
        return Response(stdout="passthrough", exit_code=5)

    with IPCServer(
        socket_path,
        handler=handler,
        passthrough_handler=passthrough_handler,
    ):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        result = PassthroughResult(
            invocation_id="123",
            stdout="out",
            stderr="err",
            exit_code=0,
        )
        response = report_passthrough_result(result, timeout=1.0)

    assert seen
    assert seen[0].invocation_id == "123"
    assert response.stdout == "passthrough"
    assert response.exit_code == 5
