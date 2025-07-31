"""Tests for replay error cleanup logic."""

from __future__ import annotations

import os
import typing as t

import pytest

import cmd_mox.controller as controller
from cmd_mox import CmdMox


def test_replay_cleanup_on_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure environment is restored when replay setup fails."""
    mox = CmdMox()
    pre_env = os.environ.copy()
    mox.__enter__()

    def boom(*_args: object, **_kwargs: object) -> t.NoReturn:
        raise RuntimeError("boom")

    monkeypatch.setattr(controller, "create_shim_symlinks", boom)

    with pytest.raises(RuntimeError):
        mox.replay()

    assert mox._server is None
    assert not mox._entered
    assert os.environ == pre_env
