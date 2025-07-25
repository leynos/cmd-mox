"""Unit tests for :mod:`cmd_mox.controller`."""

from __future__ import annotations

import os
import subprocess
import tempfile
import typing as t
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox, MockCommand, SpyCommand, StubCommand
from cmd_mox.errors import (
    LifecycleError,
    MissingEnvironmentError,
    UnexpectedCommandError,
)
from cmd_mox.ipc import Response

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from cmd_mox.ipc import Invocation


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


def test_stub_idempotency() -> None:
    """Repeated calls to stub() with the same name return the same object."""
    mox = CmdMox()
    s1 = mox.stub("bar")
    s2 = mox.stub("bar")
    assert s1 is s2


def test_spy_idempotency() -> None:
    """Repeated calls to spy() with the same name return the same object."""
    mox = CmdMox()
    s1 = mox.spy("bar")
    s2 = mox.spy("bar")
    assert s1 is s2


def test_double_kind_mismatch() -> None:
    """Requesting a different kind for an existing double raises ``ValueError``."""
    mox = CmdMox()
    mox.stub("foo")
    with pytest.raises(ValueError, match="registered as stub"):
        mox.mock("foo")


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


def test_invocation_order_multiple_calls() -> None:
    """Multiple calls are recorded in order."""
    mox = CmdMox()
    mox.mock("hello").returns(stdout="hi").times(2)
    mox.spy("world").returns(stdout="earth")
    mox.__enter__()
    mox.replay()

    cmd_hello = Path(mox.environment.shim_dir) / "hello"
    cmd_world = Path(mox.environment.shim_dir) / "world"
    subprocess.run([str(cmd_hello)], capture_output=True, text=True, check=True)  # noqa: S603
    subprocess.run([str(cmd_world)], capture_output=True, text=True, check=True)  # noqa: S603
    subprocess.run([str(cmd_hello)], capture_output=True, text=True, check=True)  # noqa: S603

    mox.verify()

    assert [inv.command for inv in mox.journal] == ["hello", "world", "hello"]
    assert len(mox.mocks["hello"].invocations) == 2
    assert len(mox.spies["world"].invocations) == 1


def test_context_manager_restores_env_on_exception() -> None:
    """Context manager restores environment even if an exception occurs."""

    class CustomError(Exception):
        """Exception used to trigger cleanup."""

    def run_with_error() -> None:
        with mox:
            mox.stub("boom").returns(stdout="oops")
            mox.replay()
            cmd_path = Path(mox.environment.shim_dir) / "boom"
            subprocess.run(  # noqa: S603
                [str(cmd_path)], capture_output=True, text=True, check=True
            )
            raise CustomError

    original_env = os.environ.copy()
    mox = CmdMox()
    with pytest.raises(CustomError):
        run_with_error()

    assert os.environ == original_env


def test_context_manager_auto_verify() -> None:
    """Exiting the context automatically calls verify."""
    mox = CmdMox()
    mox.stub("hi").returns(stdout="hello")
    with mox:
        mox.replay()
        cmd_path = Path(mox.environment.shim_dir) / "hi"
        subprocess.run([str(cmd_path)], capture_output=True, text=True, check=True)  # noqa: S603

    with pytest.raises(LifecycleError):
        mox.verify()


def test_is_recording_property() -> None:
    """is_recording is True for mocks and spies, False for stubs."""
    mox = CmdMox()
    stub = mox.stub("a")
    mock = mox.mock("b")
    spy = mox.spy("c")

    assert not stub.is_recording
    assert mock.is_recording
    assert spy.is_recording


def _tuple_handler(invocation: Invocation) -> tuple[str, str, int]:
    assert invocation.args == []
    return ("handled", "", 0)


def _response_handler(invocation: Invocation) -> Response:
    assert invocation.args == []
    return Response(stdout="r", stderr="", exit_code=0)


@pytest.mark.parametrize(
    ("cmd", "handler", "expected"),
    [
        ("dyn", _tuple_handler, "handled"),
        ("obj", _response_handler, "r"),
    ],
)
def test_stub_runs_handler(
    cmd: str,
    handler: t.Callable[[Invocation], Response | tuple[str, str, int]],
    expected: str,
) -> None:
    """Stub runs a dynamic handler when invoked."""
    mox = CmdMox()
    mox.stub(cmd).runs(handler)
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / cmd
    result = subprocess.run(  # noqa: S603
        [str(cmd_path)], capture_output=True, text=True, check=True
    )

    mox.verify()

    assert result.stdout.strip() == expected
