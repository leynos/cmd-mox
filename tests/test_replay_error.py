"""Tests for replay error cleanup logic."""

from __future__ import annotations

import contextlib
import os
import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    from types import TracebackType

import pytest

from cmd_mox import CmdMox, controller

pytestmark = [pytest.mark.requires_unix_sockets]


def test_replay_cleanup_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure environment is restored when replay setup fails."""
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

    def boom(*_args: object, **_kwargs: object) -> typ.NoReturn:
        raise RuntimeError("boom")

    # The stack guarantees the controller is exited even if an assertion below
    # fails. replay() is expected to fail and clean up on its own, so once the
    # assertions confirm that self-cleanup the stack's callback is discarded to
    # avoid a second exit.
    with contextlib.ExitStack() as stack:
        stack.enter_context(mox)

        monkeypatch.setattr(CmdMox, "__exit__", fake_exit)
        monkeypatch.setattr(controller, "create_shim_symlinks", boom)

        with pytest.raises(RuntimeError):
            mox.replay()

        assert called == [(None, None, None)], "replay() should exit the controller"
        assert mox._server is None, "replay() failure should clear the IPC server"
        assert not mox._entered, "replay() failure should leave the controller exited"
        assert os.environ == pre_env, "replay() failure should restore the environment"
        stack.pop_all()


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
    assert exc_type is BoomError, "__exit__ should receive the raised exception type"
    assert isinstance(exc, BoomError), "__exit__ should receive the exception instance"
    assert tb is not None, "__exit__ should receive a traceback"
    assert mox._server is None, "exceptional exit should clear the IPC server"
    assert not mox._entered, "exceptional exit should leave the controller exited"
    assert os.environ == pre_env, "exceptional exit should restore the environment"
