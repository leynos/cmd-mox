"""Tests covering :class:`cmd_mox.ipc.IPCServer` callback behaviour."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import (
    Invocation,
    IPCHandlers,
    IPCServer,
    PassthroughResult,
    Response,
    invoke_server,
    report_passthrough_result,
)

if t.TYPE_CHECKING:
    from pathlib import Path


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

    assert response.stdout == "cmd"
    assert response.stderr == ""
    assert response.exit_code == 0


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

    with IPCServer(socket_path, handlers=IPCHandlers(handler=handler)):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="cmd", args=["--flag"], stdin="", env={})
        response = invoke_server(invocation, timeout=1.0)

    assert seen
    assert seen[0].command == "cmd"
    assert response.stdout == "handled"
    assert response.stderr == "err"
    assert response.exit_code == 2


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
        with pytest.raises(RuntimeError, match="Invalid JSON from IPC server"):
            report_passthrough_result(result, timeout=1.0)


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
        handlers=IPCHandlers(
            handler=handler,
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

    assert seen
    assert seen[0].invocation_id == "123"
    assert response.stdout == "passthrough"
    assert response.exit_code == 5


def test_handle_invocation_default(tmp_path: Path) -> None:
    """Direct invocation handling should echo when no handler is set."""
    server = IPCServer(tmp_path / "ipc.sock")
    invocation = Invocation(command="cmd", args=["--flag"], stdin="", env={})

    response = server.handle_invocation(invocation)

    assert response.stdout == "cmd"
    assert response.stderr == ""
    assert response.exit_code == 0


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

    assert [item.command for item in seen] == ["cmd"]
    assert response.stdout == "handled"
    assert response.stderr == "err"
    assert response.exit_code == 3


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

    assert isinstance(excinfo.value.__cause__, ValueError)
