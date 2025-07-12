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
        server = IPCServer(socket_path)
        server.start()
        create_shim_symlinks(env.shim_dir, commands)

        os.environ[CMOX_IPC_SOCKET_ENV] = str(socket_path)
        result = subprocess.run(  # noqa: S603
            [str(env.shim_dir / "foo")],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "foo"
        server.stop()
