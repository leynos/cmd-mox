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
    # Entered deliberately without a matching exit: replay() is expected to
    # fail before an exit would normally occur, and the assertions below
    # verify the controller cleans up its own state on that failure path.
    contextlib.ExitStack().enter_context(mox)

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

    monkeypatch.setattr(CmdMox, "__exit__", fake_exit)
    monkeypatch.setattr(controller, "create_shim_symlinks", boom)

    with pytest.raises(RuntimeError):
        mox.replay()

    assert called == [(None, None, None)], "Assertion failed"
    assert mox._server is None, "Assertion failed"
    assert not mox._entered, "Assertion failed"
    assert os.environ == pre_env, "Assertion failed"


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
    assert exc_type is BoomError, "Assertion failed"
    assert isinstance(exc, BoomError), "Assertion failed"
    assert tb is not None, "Assertion failed"
    assert mox._server is None, "Assertion failed"
    assert not mox._entered, "Assertion failed"
    assert os.environ == pre_env, "Assertion failed"
