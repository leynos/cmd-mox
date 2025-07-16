"""Unit tests for :mod:`cmd_mox.controller`."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox


def test_cmdmox_stub_records_invocation() -> None:
    """Stubbed command returns configured output and journal records call."""
    original_path = os.environ["PATH"]
    mox = CmdMox()
    mox.stub("hello").returns(stdout="hi")
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hello"
    result = subprocess.run(  # noqa: S603
        [str(cmd_path)], capture_output=True, text=True, check=True
    )
    mox.verify()

    assert result.stdout.strip() == "hi"
    assert len(mox.journal) == 1
    assert mox.journal[0].command == "hello"
    assert os.environ["PATH"] == original_path


def test_cmdmox_replay_verify_out_of_order() -> None:
    """Calling replay() or verify() out of order should raise RuntimeError."""
    mox = CmdMox()
    with pytest.raises(RuntimeError):
        mox.verify()
    mox.stub("foo").returns(stdout="bar")
    mox.replay()
    with pytest.raises(RuntimeError):
        mox.replay()
    cmd_path = Path(mox.environment.shim_dir) / "foo"
    subprocess.run([str(cmd_path)], capture_output=True, text=True, check=True)  # noqa: S603
    mox.verify()
    with pytest.raises(RuntimeError):
        mox.verify()


def test_cmdmox_nonstubbed_command_returns_name() -> None:
    """Invoking a non-stubbed command returns command name as stdout."""
    mox = CmdMox()
    mox.register_command("not_stubbed")
    mox.replay()
    cmd_path = Path(mox.environment.shim_dir) / "not_stubbed"
    result = subprocess.run(  # noqa: S603
        [str(cmd_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    with pytest.raises(AssertionError):
        mox.verify()
    mox.__exit__(None, None, None)
    assert result.stdout.strip() == "not_stubbed"


def test_cmdmox_environment_cleanup_on_exception() -> None:
    """Environment is cleaned up if an exception occurs during test."""
    original_path = os.environ["PATH"]
    mox = CmdMox()
    mox.stub("fail").returns(stdout="fail")
    mox.replay()

    def _boom() -> None:
        raise RuntimeError

    try:
        _boom()
    except RuntimeError:
        pass
    finally:
        with pytest.raises(AssertionError):
            mox.verify()
        mox.__exit__(None, None, None)
    assert os.environ["PATH"] == original_path


def test_cmdmox_missing_environment_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Behavior when environment is not initialized."""
    mox = CmdMox()
    mox.stub("foo").returns(stdout="bar")
    mox.replay()
    monkeypatch.setattr(mox.environment, "shim_dir", None)
    with pytest.raises(TypeError):
        Path(mox.environment.shim_dir) / "foo"  # type: ignore[arg-type]
    monkeypatch.setattr(mox.environment, "socket_path", None)
    assert mox.environment.socket_path is None
    with pytest.raises(AssertionError):
        mox.verify()
    mox.__exit__(None, None, None)
