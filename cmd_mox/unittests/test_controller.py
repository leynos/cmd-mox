"""Unit tests for :mod:`cmd_mox.controller`."""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox, MockCommand, SpyCommand, StubCommand
from cmd_mox.errors import (
    LifecycleError,
    MissingEnvironmentError,
    UnexpectedCommandError,
    UnfulfilledExpectationError,
)


def test_cmdmox_stub_records_invocation() -> None:
    """Stubbed command returns configured output and journal records call."""
    original_path = os.environ["PATH"]
    mox = CmdMox()
    mox.stub("hello").returns(stdout="hi")
    mox.__enter__()
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
    with pytest.raises(LifecycleError):
        mox.verify()
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()
    mox.replay()
    with pytest.raises(LifecycleError):
        mox.replay()
    cmd_path = Path(mox.environment.shim_dir) / "foo"
    subprocess.run([str(cmd_path)], capture_output=True, text=True, check=True)  # noqa: S603
    mox.verify()
    with pytest.raises(LifecycleError):
        mox.verify()


def test_cmdmox_nonstubbed_command_behavior() -> None:
    """Invoking a non-stubbed command returns name but fails verification."""
    mox = CmdMox()
    mox.register_command("not_stubbed")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "not_stubbed"
    result = subprocess.run(  # noqa: S603
        [str(cmd_path)], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "not_stubbed"

    with pytest.raises(UnexpectedCommandError):
        mox.verify()


def _test_environment_cleanup_helper(*, call_replay_before_exception: bool) -> None:
    """Shared logic verifying env cleanup when exceptions occur."""
    original_path = os.environ["PATH"]
    mox = CmdMox()
    mox.stub("fail").returns(stdout="fail")
    mox.__enter__()
    if call_replay_before_exception:
        mox.replay()

    # Environment should differ while the manager is active
    assert os.environ["PATH"] != original_path

    def _boom() -> None:
        raise RuntimeError

    try:
        _boom()
    except RuntimeError:
        pass
    finally:
        if call_replay_before_exception:
            with pytest.raises(UnfulfilledExpectationError):
                mox.verify()
        mox.__exit__(None, None, None)

    # Ensure PATH is fully restored
    assert os.environ["PATH"] == original_path


def test_cmdmox_environment_cleanup_on_exception() -> None:
    """Environment is cleaned when an exception occurs after replay."""
    _test_environment_cleanup_helper(call_replay_before_exception=True)


def test_cmdmox_environment_cleanup_on_exception_before_replay() -> None:
    """Environment is cleaned up if an error occurs before replay."""
    _test_environment_cleanup_helper(call_replay_before_exception=False)


def test_cmdmox_missing_environment_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay fails when environment attributes are missing."""
    mox = CmdMox()
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()

    monkeypatch.setattr(mox.environment, "shim_dir", None)
    with pytest.raises(MissingEnvironmentError, match="shim_dir"):
        mox.replay()

    # Restore shim_dir and remove socket_path
    monkeypatch.setattr(mox.environment, "shim_dir", Path(tempfile.gettempdir()))
    monkeypatch.setattr(mox.environment, "socket_path", None)
    with pytest.raises(MissingEnvironmentError, match="socket_path"):
        mox.replay()

    mox.__exit__(None, None, None)


def test_factory_methods_create_distinct_objects() -> None:
    """CmdMox exposes mock() and spy() alongside stub()."""
    mox = CmdMox()
    assert isinstance(mox.stub("a"), StubCommand)
    assert isinstance(mox.mock("b"), MockCommand)
    assert isinstance(mox.spy("c"), SpyCommand)


def test_mock_idempotency() -> None:
    """Repeated calls to mock() with the same name return the same object."""
    mox = CmdMox()
    m1 = mox.mock("foo")
    m2 = mox.mock("foo")
    assert m1 is m2


def test_spy_idempotency() -> None:
    """Repeated calls to spy() with the same name return the same object."""
    mox = CmdMox()
    s1 = mox.spy("bar")
    s2 = mox.spy("bar")
    assert s1 is s2


def test_mock_and_spy_invocations() -> None:
    """Mock and spy commands record calls and verify correctly."""
    mox = CmdMox()
    mox.mock("hello").returns(stdout="hi")
    mox.spy("world").returns(stdout="earth")
    mox.__enter__()
    mox.replay()

    cmd_hello = Path(mox.environment.shim_dir) / "hello"
    cmd_world = Path(mox.environment.shim_dir) / "world"
    res1 = subprocess.run([str(cmd_hello)], capture_output=True, text=True, check=True)  # noqa: S603
    res2 = subprocess.run([str(cmd_world)], capture_output=True, text=True, check=True)  # noqa: S603

    mox.verify()

    assert res1.stdout.strip() == "hi"
    assert res2.stdout.strip() == "earth"
    assert len(mox.journal) == 2
    assert mox.mocks["hello"].invocations[0].command == "hello"
    assert mox.spies["world"].invocations[0].command == "world"
