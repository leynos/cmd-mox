"""Unit and behavioural tests for environment helper utilities."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.environment import EnvironmentManager
from cmd_mox.errors import MissingEnvironmentError
from cmd_mox.unittests._env_helpers import (
    require_shim_dir,
    require_socket_path,
)
from cmd_mox.unittests.conftest import run_subprocess

if t.TYPE_CHECKING:  # pragma: no cover - typing helper
    from pathlib import Path


def test_require_shim_dir_returns_path_when_set(tmp_path: Path) -> None:
    """Helper should return the configured shim directory."""
    env = EnvironmentManager()
    env.shim_dir = tmp_path
    assert require_shim_dir(env) is tmp_path


def test_require_shim_dir_raises_when_missing() -> None:
    """Helper should raise if shim_dir is not initialised."""
    env = EnvironmentManager()
    env.shim_dir = None
    with pytest.raises(MissingEnvironmentError):
        require_shim_dir(env)


def test_require_socket_path_returns_path_when_set(tmp_path: Path) -> None:
    """Helper should return the configured socket path."""
    env = EnvironmentManager()
    env.socket_path = tmp_path / "ipc.sock"
    assert require_socket_path(env) == tmp_path / "ipc.sock"


def test_require_socket_path_raises_when_missing() -> None:
    """Helper should raise if socket_path is not initialised."""
    env = EnvironmentManager()
    env.socket_path = None
    with pytest.raises(MissingEnvironmentError):
        require_socket_path(env)


def test_require_helpers_with_active_environment(tmp_path: Path) -> None:
    """Helpers work in a real EnvironmentManager context (behavioural)."""
    with EnvironmentManager() as env:
        shim_dir = require_shim_dir(env)
        socket_path = require_socket_path(env)
        assert shim_dir.exists()
        assert socket_path.parent == shim_dir

        shim_path = shim_dir / "echo"
        shim_path.write_text("#!/bin/sh\necho helper-ok\n", encoding="utf-8")
        shim_path.chmod(0o755)

        result = run_subprocess([str(shim_path)], env={"PATH": str(shim_dir)})
        assert "helper-ok" in result.stdout
