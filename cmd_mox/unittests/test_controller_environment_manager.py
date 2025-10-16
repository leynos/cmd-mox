"""Unit tests for :mod:`cmd_mox.controller` - environment manager error handling."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.errors import MissingEnvironmentError

pytestmark = pytest.mark.requires_unix_sockets


def test_cmdmox_missing_environment_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay fails when environment attributes are missing."""
    mox = CmdMox()
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()

    monkeypatch.setattr(mox.environment, "shim_dir", None)
    with pytest.raises(MissingEnvironmentError, match="shim_dir"):
        mox.replay()

    # Restore shim_dir and remove socket_path
    monkeypatch.setattr(mox.environment, "shim_dir", Path(tempfile.gettempdir()))
    monkeypatch.setattr(mox.environment, "socket_path", None)
    with pytest.raises(MissingEnvironmentError, match="socket_path"):
        mox.replay()

    # Use the public context-manager API to restore PATH and other state.
    # Calling the private _stop_server_and_exit_env helper would bypass
    # type checking, so tests rely on __exit__ instead.
    mox.__exit__(None, None, None)


def test_require_env_attrs(monkeypatch: pytest.MonkeyPatch) -> None:
    """_require_env_attrs reports missing EnvironmentManager attributes."""
    mox = CmdMox()
    mox.__enter__()
    monkeypatch.setattr(mox.environment, "shim_dir", None)
    monkeypatch.setattr(mox.environment, "socket_path", None)
    with pytest.raises(MissingEnvironmentError, match="shim_dir, socket_path"):
        mox._require_env_attrs("shim_dir", "socket_path")
    mox.__exit__(None, None, None)


def test_verify_missing_environment_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify() fails when environment attributes are missing."""
    mox = CmdMox(verify_on_exit=False)  # Disable auto-verify to avoid double error
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()
    mox.replay()

    monkeypatch.setattr(mox.environment, "shim_dir", None)
    monkeypatch.setattr(mox.environment, "socket_path", None)
    with pytest.raises(MissingEnvironmentError, match=r"shim_dir.*socket_path"):
        mox.verify()
    mox.__exit__(None, None, None)
