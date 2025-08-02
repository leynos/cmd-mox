"""Tests for replay error cleanup logic."""

from __future__ import annotations

import os
import typing as t

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from types import TracebackType

import pytest

import cmd_mox.controller as controller
from cmd_mox import CmdMox


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
