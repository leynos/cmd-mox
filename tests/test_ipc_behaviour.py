"""Behavioural tests for shim execution through the IPC server."""

from __future__ import annotations

import logging
import os
import subprocess
import typing as t

import pytest

from cmd_mox import EnvironmentManager, IPCServer, create_shim_symlinks
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, CMOX_IPC_TIMEOUT_ENV
from cmd_mox.unittests.test_invocation_journal import _shim_cmd_path

pytestmark = pytest.mark.requires_unix_sockets

if t.TYPE_CHECKING:  # pragma: no cover - imported for type checking only
    from pathlib import Path


def _run_shim(
    env: EnvironmentManager, command: str
) -> subprocess.CompletedProcess[str]:
    """Execute *command* through its generated shim."""
    shim_path = _shim_cmd_path(env, command)
    return subprocess.run(  # noqa: S603
        [str(shim_path)],
        capture_output=True,
        text=True,
        check=True,
    )


def _invoke_command_via_ipc(
    env: EnvironmentManager,
    command: str,
    *,
    timeout: float | None = None,
) -> subprocess.CompletedProcess[str]:
    """Start an IPC server and run *command* through its shim."""
    assert env.shim_dir is not None
    socket_path = env.socket_path
    assert socket_path is not None
    server_timeout = timeout if timeout is not None else 5.0

    with IPCServer(socket_path, timeout=server_timeout):
        create_shim_symlinks(env.shim_dir, [command])

        expected_timeout = str(server_timeout)
        assert os.environ[CMOX_IPC_SOCKET_ENV] == str(socket_path)
        assert os.environ[CMOX_IPC_TIMEOUT_ENV] == expected_timeout

        return _run_shim(env, command)


def test_shim_invokes_via_ipc() -> None:
    """End-to-end shim invocation using the IPC server."""
    with EnvironmentManager() as env:
        result = _invoke_command_via_ipc(env, "foo")
        assert result.stdout.strip() == "foo"
        assert result.stderr == ""
        assert result.returncode == 0


def test_ipc_server_exports_custom_timeout() -> None:
    """Starting the server with a custom timeout updates the environment."""
    with EnvironmentManager() as env:
        result = _invoke_command_via_ipc(env, "qux", timeout=1.25)
        assert os.environ[CMOX_IPC_TIMEOUT_ENV] == "1.25"
        assert result.stdout.strip() == "qux"


def test_shim_errors_when_socket_unset() -> None:
    """Shim prints an error if IPC socket env var is missing."""
    commands = ["bar"]
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        create_shim_symlinks(env.shim_dir, commands)
        os.environ.pop(CMOX_IPC_SOCKET_ENV, None)
        result = subprocess.run(  # noqa: S603
            [str(_shim_cmd_path(env, "bar"))],
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""
        assert result.stderr.strip() == "IPC socket not specified"
        assert result.returncode == 1


def test_shim_errors_on_invalid_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shim prints an error if timeout env var is invalid."""
    commands = ["baz"]
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        create_shim_symlinks(env.shim_dir, commands)
        monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, "dummy")
        monkeypatch.setenv(CMOX_IPC_TIMEOUT_ENV, "nan")
        result = subprocess.run(  # noqa: S603
            [str(_shim_cmd_path(env, "baz"))],
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""
        assert "invalid timeout: 'nan'" in result.stderr
        assert result.returncode == 1


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
        assert env.shim_dir is not None
        original = env.shim_dir
        env.shim_dir = replacement

    assert original is not None
    assert original.exists()
    assert replacement.exists()
    # The manager should drop ownership of the replacement directory when skipping.
    assert env.shim_dir is None
    assert any(
        record.levelno == logging.WARNING
        and record.message.startswith(
            "Skipping cleanup for original temporary directory"
        )
        for record in caplog.records
    ), caplog.text
