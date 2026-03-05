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
    """Replay is idempotent in replay phase and strict elsewhere."""
    mox = CmdMox()
    with pytest.raises(LifecycleError):
        mox.verify()
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()
    mox.replay()
    cmd_path = require_shim_dir(mox.environment) / "foo"
    run([str(cmd_path)])
    assert len(mox.journal) == 1
    # Second replay() must be a no-op: phase stays REPLAY and the
    # journal is not cleared (a real restart calls journal.clear()).
    mox.replay()
    assert mox.phase is Phase.REPLAY
    assert len(mox.journal) == 1
    mox.verify()
    with pytest.raises(LifecycleError):
        mox.verify()
    with pytest.raises(LifecycleError):
        mox.replay()


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


def test_replay_after_exit_without_verify_raises() -> None:
    """Replay after context exit without verify must raise.

    When ``verify_on_exit=False`` the context manager tears down the
    IPC server and clears ``_entered`` but leaves the phase as
    ``REPLAY``.  A subsequent ``replay()`` call must not be treated as
    an idempotent no-op; it should raise because the context is no
    longer active.

    Raises
    ------
    LifecycleError
        Expected when ``replay()`` is called after the context has
        exited with ``verify_on_exit=False``.
    """
    mox = CmdMox(verify_on_exit=False)
    mox.stub("dummy").returns(stdout="ok")
    with mox:
        mox.replay()
    # Context exited: server stopped, _entered=False, phase still REPLAY.
    with pytest.raises(LifecycleError):
        mox.replay()


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


def test_verify_cleans_up_when_recording_finalize_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IPC server stops and environment restores despite recording errors.

    Regression: if recording session finalization and environment cleanup
    share the same finally block without independent guarding, an I/O
    error during fixture persistence prevents mandatory IPC shutdown and
    environment restoration.
    """
    mox = CmdMox()
    mox.stub("dummy").returns(stdout="ok")
    mox.__enter__()
    mox.replay()

    def _boom() -> None:
        raise OSError("boom")

    monkeypatch.setattr(mox, "_finalize_recording_sessions", _boom)

    with pytest.raises(OSError, match="boom"):
        mox.verify()

    # Mandatory cleanup must have happened despite the OSError.
    assert EnvironmentManager.get_active_manager() is None
    assert not mox._entered
    assert mox.phase is Phase.VERIFY
