"""Tests for spy assertion helpers."""

import subprocess
import typing as t
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox


def test_spy_assert_called_and_called_with(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Spy exposes assert helpers mirroring unittest.mock."""
    mox = CmdMox()
    spy = mox.spy("hi").returns(stdout="hello")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hi"
    run([str(cmd_path), "foo", "bar"])

    mox.verify()

    spy.assert_called()
    spy.assert_called_with("foo", "bar")


def test_spy_assert_called_raises_when_never_called() -> None:
    """assert_called raises when the spy was never invoked."""
    mox = CmdMox()
    spy = mox.spy("hi")
    mox.__enter__()
    mox.replay()
    mox.verify()

    with pytest.raises(AssertionError):
        spy.assert_called()


def test_spy_assert_called_with_mismatched_args(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """assert_called_with raises when arguments differ."""
    mox = CmdMox()
    spy = mox.spy("hi").returns(stdout="hello")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hi"
    run([str(cmd_path), "actual"])

    mox.verify()

    with pytest.raises(AssertionError):
        spy.assert_called_with("expected")
