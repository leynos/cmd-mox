"""Unit tests for :mod:`cmd_mox.environment`."""

from __future__ import annotations

import os
import typing as t

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, EnvironmentManager

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import pytest


def test_environment_manager_modifies_and_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def test_environment_restores_modified_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    """User-modified variables inside context should revert on exit."""
    os.environ["TEST_VAR"] = "before"
    with EnvironmentManager():
        os.environ["TEST_VAR"] = "inside"
    assert os.environ["TEST_VAR"] == "before"
