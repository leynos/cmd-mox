"""Tests for shim generation utilities."""

import os
import subprocess

from cmd_mox.environment import EnvironmentManager
from cmd_mox.shimgen import SHIM_PATH, create_shim_symlinks


def test_create_shim_symlinks_and_execution() -> None:
    """Symlinks execute the shim and expose the invoked name."""
    commands = ["git", "curl"]
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        mapping = create_shim_symlinks(env.shim_dir, commands)
        assert set(mapping) == set(commands)
        for cmd in commands:
            link = mapping[cmd]
            assert link.is_symlink()
            assert link.resolve() == SHIM_PATH
            assert os.access(link, os.X_OK)
            result = subprocess.run(  # noqa: S603
                [str(link)],
                check=True,
                capture_output=True,
                text=True,
            )
            assert result.stdout.strip() == cmd
