"""Tests for replay error cleanup logic."""

from __future__ import annotations

import os
import typing as t
from pathlib import Path

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from types import TracebackType

import pytest

import cmd_mox.controller as controller
from cmd_mox import CmdMox
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, EnvironmentManager

pytestmark = pytest.mark.requires_unix_sockets


def test_replay_cleanup_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure environment is restored when replay setup fails."""
    mox = CmdMox()
    pre_env = os.environ.copy()
    mox.__enter__()

    called: list[
        tuple[type[BaseException] | None, BaseException | None, TracebackType | None]
    ] = []
    orig_exit = CmdMox.__exit__

    def fake_exit(
        self: CmdMox,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        called.append((exc_type, exc, tb))
        orig_exit(self, exc_type, exc, tb)

    def boom(*_args: object, **_kwargs: object) -> t.NoReturn:
        raise RuntimeError("boom")

    monkeypatch.setattr(CmdMox, "__exit__", fake_exit)
    monkeypatch.setattr(controller, "create_shim_symlinks", boom)

    with pytest.raises(RuntimeError):
        mox.replay()

    assert called == [(None, None, None)]
    assert mox._server is None
    assert not mox._entered
    assert os.environ == pre_env


def test_replay_cleanup_on_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """KeyboardInterrupt during replay startup should restore the environment."""
    mox = CmdMox()
    pre_env = os.environ.copy()
    mox.__enter__()

    assert isinstance(mox.environment, EnvironmentManager)
    env = mox.environment
    assert env.shim_dir is not None
    assert env.socket_path is not None
    shim_dir = Path(env.shim_dir)
    socket_path = Path(env.socket_path)
    assert shim_dir.exists()

    def raise_interrupt() -> t.NoReturn:
        raise KeyboardInterrupt

    monkeypatch.setattr(mox, "_start_ipc_server", raise_interrupt)

    with pytest.raises(KeyboardInterrupt):
        mox.replay()

    assert os.environ == pre_env
    assert EnvironmentManager.get_active_manager() is None
    assert mox._server is None
    assert not mox._entered
    assert not shim_dir.exists()
    assert not socket_path.exists()
    if CMOX_IPC_SOCKET_ENV in pre_env:
        assert os.environ[CMOX_IPC_SOCKET_ENV] == pre_env[CMOX_IPC_SOCKET_ENV]
    else:
        assert CMOX_IPC_SOCKET_ENV not in os.environ


def test_exit_receives_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Propagate exception details to :meth:`CmdMox.__exit__`."""
    mox = CmdMox()
    pre_env = os.environ.copy()

    called: list[
        tuple[type[BaseException] | None, BaseException | None, TracebackType | None]
    ] = []
    orig_exit = CmdMox.__exit__

    def fake_exit(
        self: CmdMox,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        called.append((exc_type, exc, tb))
        orig_exit(self, exc_type, exc, tb)

    monkeypatch.setattr(CmdMox, "__exit__", fake_exit)

    class BoomError(RuntimeError):
        """Sentinel error used to trigger exceptional exit."""

    def trigger() -> None:
        with mox:
            mox.replay()
            raise BoomError("boom")

    with pytest.raises(BoomError):
        trigger()

    exc_type, exc, tb = called[0]
    assert exc_type is BoomError
    assert isinstance(exc, BoomError)
    assert tb is not None
    assert mox._server is None
    assert not mox._entered
    assert os.environ == pre_env
