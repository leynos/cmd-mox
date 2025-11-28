"""Unit tests for :mod:`cmd_mox.controller` - lifecycle and phase management."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.controller import CmdMox, Phase
from cmd_mox.environment import EnvironmentManager
from cmd_mox.errors import LifecycleError
from cmd_mox.unittests._env_helpers import (
    require_shim_dir,
    require_socket_path,
)

pytestmark = pytest.mark.requires_unix_sockets

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    import subprocess


def test_cmdmox_replay_verify_out_of_order(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Calling replay() or verify() out of order should raise LifecycleError."""
    mox = CmdMox()
    with pytest.raises(LifecycleError):
        mox.verify()
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()
    mox.replay()
    with pytest.raises(LifecycleError):
        mox.replay()
    cmd_path = require_shim_dir(mox.environment) / "foo"
    run([str(cmd_path)])
    mox.verify()
    with pytest.raises(LifecycleError):
        mox.verify()


def test_phase_property_tracks_lifecycle() -> None:
    """The phase property reflects lifecycle transitions."""
    mox = CmdMox()
    assert mox.phase is Phase.RECORD

    mox.__enter__()
    mox.replay()
    assert mox.phase is Phase.REPLAY

    mox.verify()
    assert mox.phase is Phase.VERIFY


def test_require_phase_mismatch() -> None:
    """_require_phase raises when current phase does not match."""
    mox = CmdMox()
    with pytest.raises(LifecycleError, match="not in 'replay' phase"):
        mox._require_phase(Phase.REPLAY, "replay")


def test_context_manager_auto_verify(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Exiting the context automatically calls verify."""
    mox = CmdMox()
    mox.stub("hi").returns(stdout="hello")
    with mox:
        mox.replay()
        cmd_path = require_shim_dir(mox.environment) / "hi"
        run([str(cmd_path)])

    with pytest.raises(LifecycleError):
        mox.verify()


def test_replay_cleans_up_on_keyboard_interrupt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup must run even when replay is interrupted by BaseException."""
    mox = CmdMox()
    env = mox.environment
    assert env is not None

    mox.__enter__()
    assert env.shim_dir is not None
    assert env.socket_path is not None
    shim_dir = require_shim_dir(env)
    socket_path = require_socket_path(env)
    assert shim_dir.exists()

    def raise_interrupt() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(mox, "_start_ipc_server", raise_interrupt)

    with pytest.raises(KeyboardInterrupt):
        mox.replay()

    assert EnvironmentManager.get_active_manager() is None
    assert not shim_dir.exists()
    assert not socket_path.exists()
    assert not mox._entered
