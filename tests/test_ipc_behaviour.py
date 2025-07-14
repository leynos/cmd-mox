"""Behavioural test for the shim and IPC server."""

import os
import subprocess

from cmd_mox import EnvironmentManager, IPCServer, create_shim_symlinks
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV


def test_shim_invokes_via_ipc() -> None:
    """End-to-end shim invocation using the IPC server."""
    commands = ["foo"]
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        socket_path = env.socket_path
        assert socket_path is not None
        with IPCServer(socket_path):
            create_shim_symlinks(env.shim_dir, commands)

            os.environ[CMOX_IPC_SOCKET_ENV] = str(socket_path)
            result = subprocess.run(  # noqa: S603
                [str(env.shim_dir / "foo")],
                capture_output=True,
                text=True,
                check=True,
            )
            assert result.stdout.strip() == "foo"
            assert result.stderr == ""
            assert result.returncode == 0


def test_shim_errors_when_socket_unset() -> None:
    """Shim prints an error if IPC socket env var is missing."""
    commands = ["bar"]
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        create_shim_symlinks(env.shim_dir, commands)
        os.environ.pop(CMOX_IPC_SOCKET_ENV, None)
        result = subprocess.run(  # noqa: S603
            [str(env.shim_dir / "bar")],
            capture_output=True,
            text=True,
        )
        assert result.stdout == ""
        assert result.stderr.strip() == "IPC socket not specified"
        assert result.returncode == 1
