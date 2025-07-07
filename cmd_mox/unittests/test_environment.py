"""Unit tests for :mod:`cmd_mox.environment`."""

from __future__ import annotations

import os
import typing as t

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, EnvironmentManager

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from pathlib import Path


def test_environment_manager_modifies_and_restores() -> None:
    """Path and env variables should be modified and later restored."""
    original_env = os.environ.copy()
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        assert env.shim_dir.exists()
        assert os.environ["PATH"].split(os.pathsep)[0] == str(env.shim_dir)
        assert os.environ[CMOX_IPC_SOCKET_ENV] == str(env.socket_path)
        os.environ["EXTRA_VAR"] = "temp"
    assert os.environ == original_env
    assert env.shim_dir is not None
    assert not env.shim_dir.exists()


def test_environment_restores_modified_vars() -> None:
    """User-modified variables inside context should revert on exit."""
    os.environ["TEST_VAR"] = "before"
    with EnvironmentManager():
        os.environ["TEST_VAR"] = "inside"
    assert os.environ["TEST_VAR"] == "before"
    del os.environ["TEST_VAR"]


def test_environment_manager_restores_on_exception() -> None:
    """Environment is restored even if the context body raises."""
    original_env = os.environ.copy()
    holder: dict[str, Path | None] = {"path": None}

    def trigger_error() -> None:
        with EnvironmentManager() as env:
            holder["path"] = env.shim_dir
            assert env.shim_dir is not None
            assert env.shim_dir.exists()
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        trigger_error()
    assert os.environ == original_env
    assert holder["path"] is not None
    assert not holder["path"].exists()


def test_environment_manager_nested_raises() -> None:
    """Nesting EnvironmentManager should raise RuntimeError."""
    original_env = os.environ.copy()
    outer = EnvironmentManager()
    with outer as env:
        with pytest.raises(RuntimeError):
            EnvironmentManager().__enter__()
        assert os.environ["PATH"].split(os.pathsep)[0] == str(env.shim_dir)
    assert os.environ == original_env


def test_environment_restores_deleted_vars() -> None:
    """Deletion of variables inside context is undone on exit."""
    os.environ["DEL_VAR"] = "before"
    with EnvironmentManager():
        del os.environ["DEL_VAR"]
    assert os.environ["DEL_VAR"] == "before"
    del os.environ["DEL_VAR"]
