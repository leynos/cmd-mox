"""Behavioural tests for shim execution through the IPC server."""

from __future__ import annotations

import json
import logging
import os
import socket
import subprocess
import threading
import time
import typing as typ

import pytest

from cmd_mox import EnvironmentManager, IPCServer, create_shim_symlinks
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, CMOX_IPC_TIMEOUT_ENV
from cmd_mox.ipc._server_core import _encode_response
from cmd_mox.ipc.models import Response
from cmd_mox.unittests.test_invocation_journal import _shim_cmd_path

pytestmark = [pytest.mark.requires_unix_sockets]

if typ.TYPE_CHECKING:  # pragma: no cover - imported for type checking only
    from pathlib import Path


def _execute_shim(
    env: EnvironmentManager,
    command: str,
    *,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    """Execute a generated shim with the requested failure contract.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process from running the shim.
    """
    shim_path = _shim_cmd_path(env, command)
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - the test executes a command path prepared by the test harness
        [str(shim_path)],
        capture_output=True,
        text=True,
        check=check,
    )


def _run_shim(
    env: EnvironmentManager, command: str
) -> subprocess.CompletedProcess[str]:
    """Execute *command* through its generated shim.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process from running the shim.
    """
    return _execute_shim(env, command, check=True)


def _run_unserved_shim(
    env: EnvironmentManager, command: str
) -> subprocess.CompletedProcess[str]:
    """Execute *command* through a shim without an available IPC server.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process from running the unserved shim.
    """
    assert env.shim_dir is not None, "Assertion failed"
    create_shim_symlinks(env.shim_dir, [command])
    return _execute_shim(env, command, check=False)


def _invoke_command_via_ipc(
    env: EnvironmentManager,
    command: str,
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Start an IPC server and run *command* through its shim.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process from running the shim against the server.
    """
    assert env.shim_dir is not None, "Assertion failed"
    socket_path = env.socket_path
    assert socket_path is not None, "Assertion failed"
    server_timeout = timeout if timeout is not None else 5.0

    with IPCServer(socket_path, timeout=server_timeout):
        create_shim_symlinks(env.shim_dir, [command])

        expected_timeout = str(server_timeout)
        assert os.environ[CMOX_IPC_SOCKET_ENV] == str(socket_path), "Assertion failed"
        assert os.environ[CMOX_IPC_TIMEOUT_ENV] == expected_timeout, "Assertion failed"

        return _run_shim(env, command)


def _run_shim_against_socket_peer(
    env: EnvironmentManager,
    command: str,
    *,
    response_bytes: bytes | None,
) -> tuple[subprocess.CompletedProcess[str], float]:
    """Run a generated shim against a peer that replies or stalls.

    Returns
    -------
    tuple[subprocess.CompletedProcess[str], float]
        The subprocess result and its elapsed wall-clock duration in seconds.
    """
    assert env.shim_dir is not None, "Assertion failed"
    socket_path = env.socket_path
    assert socket_path is not None, "Assertion failed"
    create_shim_symlinks(env.shim_dir, [command])

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
                if response_bytes is None:
                    release.wait(2.0)
                else:
                    connection.sendall(response_bytes)
        except OSError:
            # The shim closes the connection after a timeout or decoded reply.
            pass

    server_thread = threading.Thread(target=serve, daemon=True)
    server_thread.start()
    started = time.monotonic()
    try:
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - the test executes a generated shim path
            [str(_shim_cmd_path(env, command))],
            capture_output=True,
            text=True,
            check=False,
            timeout=2.0,
        )
    finally:
        release.set()
        listener.close()
        server_thread.join(timeout=1.0)

    assert accepted.is_set(), "Unix peer did not receive a complete shim request"
    assert not server_thread.is_alive(), "Unix peer did not stop"
    return result, time.monotonic() - started


def test_shim_invokes_via_ipc() -> None:
    """End-to-end shim invocation using the IPC server."""
    with EnvironmentManager() as env:
        result = _invoke_command_via_ipc(env, "foo")
        assert result.stdout.strip() == "foo", "Assertion failed"
        assert not result.stderr, "Assertion failed"
        assert result.returncode == 0, "Assertion failed"


def test_ipc_server_exports_custom_timeout() -> None:
    """Starting the server with a custom timeout updates the environment."""
    with EnvironmentManager() as env:
        result = _invoke_command_via_ipc(env, "qux", timeout=1.25)
        assert os.environ[CMOX_IPC_TIMEOUT_ENV] == "1.25", "Assertion failed"
        assert result.stdout.strip() == "qux", "Assertion failed"


def test_shim_errors_when_socket_unset() -> None:
    """Shim prints an error if IPC socket env var is missing."""
    with EnvironmentManager() as env:
        os.environ.pop(CMOX_IPC_SOCKET_ENV, None)
        result = _run_unserved_shim(env, "bar")
        assert not result.stdout, "Assertion failed"
        assert result.stderr.strip() == "IPC socket not specified", "Assertion failed"
        assert result.returncode == 1, "Assertion failed"


def test_shim_errors_on_invalid_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shim prints an error if timeout env var is invalid."""
    with EnvironmentManager() as env:
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, "dummy")
        monkeypatch.setenv(CMOX_IPC_TIMEOUT_ENV, "nan")
        result = _run_unserved_shim(env, "baz")
        assert not result.stdout, "Assertion failed"
        assert "invalid timeout: 'nan'" in result.stderr, "Assertion failed"
        assert result.returncode == 1, "Assertion failed"


def test_generated_shim_reports_server_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rejection response crosses the Unix socket into the shim diagnostic."""
    with EnvironmentManager() as env:
        assert env.socket_path is not None, "EnvironmentManager did not set a socket"
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(env.socket_path))
        monkeypatch.setenv(CMOX_IPC_TIMEOUT_ENV, "1.0")
        response_bytes = _encode_response(
            Response(stderr="IPC request payload failed validation\n", exit_code=1)
        )

        result, elapsed = _run_shim_against_socket_peer(
            env,
            "rejected-command",
            response_bytes=response_bytes,
        )

    assert result.returncode == 1, "Shim did not preserve the rejection status"
    assert result.stderr == "IPC request payload failed validation\n", (
        "Shim did not print the server rejection diagnostic"
    )
    assert elapsed < 1.5, "Shim exceeded the test peer's bounded response time"


def test_generated_shim_reports_unix_response_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real Unix peer that withholds its reply produces a bounded IPC error."""
    with EnvironmentManager() as env:
        assert env.socket_path is not None, "EnvironmentManager did not set a socket"
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(env.socket_path))
        monkeypatch.setenv(CMOX_IPC_TIMEOUT_ENV, "0.2")

        result, elapsed = _run_shim_against_socket_peer(
            env,
            "stalled-command",
            response_bytes=None,
        )

    assert result.returncode == 1, "Timed-out shim did not exit non-zero"
    assert result.stderr.startswith("IPC error: "), (
        "Timed-out shim did not print a controlled IPC diagnostic"
    )
    assert elapsed < 1.5, "Shim exceeded its configured IPC timeout by too much"


def test_raw_malformed_request_gets_a_rejection_frame(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed wire bytes receive an error frame through the real server."""
    socket_path = tmp_path / "raw-ipc.sock"
    caplog.set_level(logging.INFO, logger="cmd_mox.ipc._server_core")

    with (
        IPCServer(socket_path),
        socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client,
    ):
        client.connect(str(socket_path))
        client.sendall(b"private malformed payload")
        client.shutdown(socket.SHUT_WR)
        response_chunks: list[bytes] = []
        while chunk := client.recv(1024):
            response_chunks.append(chunk)

    response = Response.from_payload(json.loads(b"".join(response_chunks)))
    assert response.stderr == "IPC request could not be parsed", (
        "Malformed request did not receive the bounded server diagnostic"
    )
    assert response.exit_code == 1, "Malformed request did not receive failure status"
    assert "private malformed payload" not in caplog.text, (
        "Server logging leaked malformed request content"
    )


def test_environment_manager_warns_when_shim_replaced(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Replacing ``shim_dir`` leaves both directories and emits a warning."""
    replacement = tmp_path / "replacement"
    replacement.mkdir()

    original: Path | None = None
    with (
        caplog.at_level(logging.WARNING, logger="cmd_mox.environment"),
        EnvironmentManager() as env,
    ):
        assert env.shim_dir is not None, "Assertion failed"
        original = env.shim_dir
        env.shim_dir = replacement

    assert original is not None, "Assertion failed"
    assert original.exists(), "Assertion failed"
    assert replacement.exists(), "Assertion failed"
    # The manager should drop ownership of the replacement directory when skipping.
    assert env.shim_dir is None, "Assertion failed"
    assert any(
        record.levelno == logging.WARNING
        and record.message.startswith(
            "Skipping cleanup for original temporary directory"
        )
        for record in caplog.records
    ), caplog.text
