"""Unit tests for :mod:`cmd_mox.controller` - environment manager error handling."""

from __future__ import annotations

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.errors import MissingEnvironmentError

pytestmark = pytest.mark.requires_unix_sockets


def test_start_ipc_server_requires_environment() -> None:
    """Starting the IPC server without an environment raises an error."""
    mox = CmdMox()
    with pytest.raises(MissingEnvironmentError, match="not initialised"):
        mox._start_ipc_server()


@pytest.mark.parametrize("attr_name", ["shim_dir", "socket_path"])
def test_cmdmox_replay_fails_when_attr_missing(
    monkeypatch: pytest.MonkeyPatch, attr_name: str
) -> None:
    """Replay fails when the specified environment attribute is missing."""
    mox = CmdMox()
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()

    monkeypatch.setattr(mox.environment, attr_name, None)
    with pytest.raises(MissingEnvironmentError, match=attr_name):
        mox.replay()

    # Use the public context-manager API to restore PATH and other state.
    # Calling the private _stop_server_and_exit_env helper would bypass
    # type checking, so tests rely on __exit__ instead.
    mox.__exit__(None, None, None)


def test_cmdmox_replay_reports_all_missing_attrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay surfaces every missing EnvironmentManager attribute."""
    mox = CmdMox()
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()

    monkeypatch.setattr(mox.environment, "shim_dir", None)
    monkeypatch.setattr(mox.environment, "socket_path", None)
    with pytest.raises(MissingEnvironmentError, match="shim_dir, socket_path"):
        mox.replay()

    # Use the public context-manager API to restore PATH and other state.
    mox.__exit__(None, None, None)


def test_verify_missing_environment_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify() fails when environment attributes are missing."""
    mox = CmdMox(
        verify_on_exit=False
    )  # Disable auto-verify (normally called by __exit__) to avoid double error
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()  # Manual context entry keeps the cleanup path explicit.
    mox.replay()

    # Monkeypatch after entering the context but before verify() runs.
    monkeypatch.setattr(mox.environment, "shim_dir", None)
    monkeypatch.setattr(mox.environment, "socket_path", None)
    with pytest.raises(MissingEnvironmentError, match=r"shim_dir.*socket_path"):
        mox.verify()
    mox.__exit__(None, None, None)


def test_replay_detects_environment_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay raises MissingEnvironmentError if shims vanish mid-start."""
    mox = CmdMox()
    env = mox.environment
    assert env is not None

    original_check = mox._check_replay_preconditions

    def tampered() -> None:
        original_check()
        env.shim_dir = None
        env.socket_path = None

    monkeypatch.setattr(mox, "_check_replay_preconditions", tampered)

    with (
        pytest.raises(
            MissingEnvironmentError, match="Replay shim directory is missing"
        ),
        mox,
    ):
        mox.replay()
