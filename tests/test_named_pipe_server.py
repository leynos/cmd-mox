"""Windows-specific behavioural tests for the named-pipe public API.

These drive :class:`~cmd_mox.ipc.NamedPipeServer` through the same client
helpers a generated shim uses, so the named-pipe transport is exercised end to
end rather than through the shared request pipeline alone.
"""

from __future__ import annotations

import os
import pathlib
import typing as typ

import pytest

from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    EnvironmentManager,
)
from cmd_mox.ipc import (
    Invocation,
    IPCHandlers,
    NamedPipeServer,
    PassthroughResult,
    Response,
    invoke_server,
    report_passthrough_result,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Named pipes require Windows")


@pytest.fixture
def passthrough_results() -> list[PassthroughResult]:
    """Return the list that the recording passthrough handler appends to.

    Returns
    -------
    list[PassthroughResult]
        A fresh, empty list shared with :func:`passthrough_handler`.
    """
    return []


@pytest.fixture
def passthrough_handler(
    passthrough_results: list[PassthroughResult],
) -> cabc.Callable[[PassthroughResult], Response]:
    """Return a handler that records passthrough results.

    Parameters
    ----------
    passthrough_results : list[PassthroughResult]
        The list the returned handler appends each received result to.

    Returns
    -------
    collections.abc.Callable[[PassthroughResult], Response]
        A handler storing each result and returning a fixed response.
    """

    def handler(result: PassthroughResult) -> Response:
        passthrough_results.append(result)
        return Response(stdout="passthrough", exit_code=5)

    return handler


def test_named_pipe_server_roundtrip(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NamedPipeServer should echo the command name without handlers."""
    socket_path = pathlib.Path(tmp_path) / "ipc.sock"
    with NamedPipeServer(socket_path, timeout=1.0):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="whoami", args=[], stdin="", env={})
        response = invoke_server(invocation, timeout=1.0)
        assert response.stdout == "whoami", "Assertion failed"


def test_named_pipe_server_exports_environment() -> None:
    """Starting NamedPipeServer under EnvironmentManager publishes env vars."""
    original_socket = os.environ.get(CMOX_IPC_SOCKET_ENV)
    original_timeout = os.environ.get(CMOX_IPC_TIMEOUT_ENV)

    with EnvironmentManager() as env:
        assert env.socket_path is not None, "Assertion failed"
        with NamedPipeServer(env.socket_path, timeout=1.5):
            assert os.environ[CMOX_IPC_SOCKET_ENV] == str(env.socket_path), (
                "Assertion failed"
            )
            assert os.environ[CMOX_IPC_TIMEOUT_ENV] == "1.5", "Assertion failed"

    assert os.environ.get(CMOX_IPC_SOCKET_ENV) == original_socket, "Assertion failed"
    assert os.environ.get(CMOX_IPC_TIMEOUT_ENV) == original_timeout, "Assertion failed"


def test_named_pipe_server_passthrough_default_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unhandled passthrough results should answer with an error response."""
    socket_path = tmp_path / "ipc.sock"

    with NamedPipeServer(socket_path, timeout=1.0):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        result = PassthroughResult(
            invocation_id="123",
            stdout="out",
            stderr="err",
            exit_code=0,
        )
        response = report_passthrough_result(result, timeout=1.0)

    assert response.exit_code == 1, "Assertion failed"
    assert "Unhandled passthrough result for 123" in response.stderr, "Assertion failed"
    assert isinstance(response.stdout, str), "stdout must be a string"
    assert not response.stdout, "Assertion failed"


def test_named_pipe_server_passthrough_handler(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    passthrough_handler: cabc.Callable[[PassthroughResult], Response],
    passthrough_results: list[PassthroughResult],
) -> None:
    """Passthrough results should reach the configured handler over the pipe."""
    socket_path = tmp_path / "ipc.sock"

    with NamedPipeServer(
        socket_path,
        timeout=1.0,
        handlers=IPCHandlers(passthrough_handler=passthrough_handler),
    ):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        result = PassthroughResult(
            invocation_id="123",
            stdout="out",
            stderr="err",
            exit_code=0,
        )
        response = report_passthrough_result(result, timeout=1.0)

    assert passthrough_results == [result], "handler must receive the result intact"
    assert response.stdout == "passthrough", "Assertion failed"
    assert response.exit_code == 5, "Assertion failed"


def test_named_pipe_server_handler_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Handler exceptions should surface as error responses over the pipe."""
    socket_path = tmp_path / "ipc.sock"

    def handler(_invocation: Invocation) -> Response:
        msg = "handler failed"
        raise RuntimeError(msg)

    with NamedPipeServer(
        socket_path,
        timeout=1.0,
        handlers=IPCHandlers(handler=handler),
    ):
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
        invocation = Invocation(command="cmd", args=[], stdin="", env={})
        response = invoke_server(invocation, timeout=1.0)

    assert response.exit_code == 1, "Assertion failed"
    assert "handler failed" in response.stderr, "Assertion failed"
    assert isinstance(response.stdout, str), "stdout must be a string"
    assert not response.stdout, "Assertion failed"
