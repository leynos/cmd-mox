"""Unit tests for :mod:`cmd_mox.controller`."""

from __future__ import annotations

import os
import tempfile
import typing as t
from pathlib import Path

import pytest

import cmd_mox.controller as controller
from cmd_mox.controller import CmdMox, Phase
from cmd_mox.errors import (
    LifecycleError,
    MissingEnvironmentError,
    UnexpectedCommandError,
)
from cmd_mox.ipc import Invocation, PassthroughResult, Response
from cmd_mox.test_doubles import MockCommand, SpyCommand, StubCommand

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess


_SYMLINK_FAILURE_MESSAGE = "symlink failure"


class _ShimSymlinkSpy:
    """Capture shim creation attempts for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, tuple[str, ...]]] = []

    def __call__(self, directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
        recorded = tuple(commands)
        self.calls.append((directory, recorded))
        return {name: directory / name for name in recorded}

    @property
    def called(self) -> bool:
        return bool(self.calls)


@pytest.fixture
def shim_symlink_spy(monkeypatch: pytest.MonkeyPatch) -> _ShimSymlinkSpy:
    """Redirect ``create_shim_symlinks`` to a spy for reuse across tests."""
    spy = _ShimSymlinkSpy()
    monkeypatch.setattr(controller, "create_shim_symlinks", spy)
    return spy


def test_cmdmox_stub_records_invocation(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Stubbed command returns configured output and journal records call."""
    original_path = os.environ["PATH"]
    mox = CmdMox()
    mox.stub("hello").returns(stdout="hi")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hello"
    result = run([str(cmd_path)])
    mox.verify()

    assert result.stdout.strip() == "hi"
    assert len(mox.journal) == 1
    assert mox.journal[0].command == "hello"
    assert os.environ["PATH"] == original_path


def test_cmdmox_replay_verify_out_of_order(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
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
    run([str(cmd_path)])
    mox.verify()
    with pytest.raises(LifecycleError):
        mox.verify()


def test_phase_property_tracks_lifecycle() -> None:
    """The phase property reflects lifecycle transitions."""
    mox = CmdMox()
    assert mox.phase is Phase.RECORD

    mox.__enter__()
    mox.replay()
    assert mox.phase is Phase.REPLAY

    mox.verify()
    assert mox.phase is Phase.VERIFY


def test_cmdmox_nonstubbed_command_behavior(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Invoking a non-stubbed command returns name but fails verification."""
    mox = CmdMox()
    mox.register_command("not_stubbed")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "not_stubbed"
    result = run([str(cmd_path)])

    assert result.stdout.strip() == "not_stubbed"

    with pytest.raises(UnexpectedCommandError):
        mox.verify()


def test_register_command_creates_shim_during_replay(
    shim_symlink_spy: _ShimSymlinkSpy,
) -> None:
    """register_command creates missing shims immediately during replay."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()
    shim_symlink_spy.calls.clear()

    mox.register_command("late")

    env = mox.environment
    assert env is not None
    assert env.shim_dir is not None
    assert shim_symlink_spy.calls == [(env.shim_dir, ("late",))]

    mox.verify()


def test_ensure_shim_during_replay_propagates_symlink_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shim creation failures bubble up so callers can surface them."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()

    def _boom(directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
        raise RuntimeError(_SYMLINK_FAILURE_MESSAGE)

    monkeypatch.setattr(controller, "create_shim_symlinks", _boom)

    with pytest.raises(RuntimeError, match=_SYMLINK_FAILURE_MESSAGE):
        mox._ensure_shim_during_replay("late")

    mox.__exit__(None, None, None)


@pytest.mark.parametrize(
    ("setup", "expected_call_count", "cleanup"),
    [
        pytest.param("no_replay", 0, None, id="outside-replay"),
        pytest.param("phase_only", 0, None, id="replay-without-environment"),
        pytest.param("full_replay", 1, "exit", id="replay-with-environment"),
    ],
)
def test_ensure_shim_during_replay_behaviour(
    setup: str,
    expected_call_count: int,
    cleanup: str | None,
    shim_symlink_spy: _ShimSymlinkSpy,
) -> None:
    """_ensure_shim_during_replay handles replay state and environment availability."""
    mox = CmdMox()

    if setup == "phase_only":
        # Directly toggle the private phase to isolate replay behaviour without
        # invoking the full environment machinery. The test accepts the tighter
        # coupling in exchange for targeting this edge case explicitly.
        mox._phase = Phase.REPLAY
    elif setup == "full_replay":
        mox.__enter__()
        mox.replay()
        shim_symlink_spy.calls.clear()

    mox._ensure_shim_during_replay("late")

    if expected_call_count:
        env = mox.environment
        assert env is not None
        assert env.shim_dir is not None
        assert shim_symlink_spy.calls == [(env.shim_dir, ("late",))]
    else:
        assert not shim_symlink_spy.called
        assert shim_symlink_spy.calls == []

    assert len(shim_symlink_spy.calls) == expected_call_count

    if cleanup == "exit":
        mox.__exit__(None, None, None)


def test_ensure_shim_during_replay_repairs_broken_symlink(
    tmp_path: Path, shim_symlink_spy: _ShimSymlinkSpy
) -> None:
    """Broken symlinks are recreated when replay is active."""
    mox = CmdMox()
    mox._phase = Phase.REPLAY
    env = mox.environment
    env.shim_dir = tmp_path

    shim_path = tmp_path / "late"
    shim_path.symlink_to(tmp_path / "missing")
    assert shim_path.is_symlink()
    assert not shim_path.exists()

    mox._ensure_shim_during_replay("late")

    assert shim_symlink_spy.calls == [(tmp_path, ("late",))]


def test_ensure_shim_during_replay_repairs_multiple_broken_symlinks(
    tmp_path: Path, shim_symlink_spy: _ShimSymlinkSpy
) -> None:
    """Each broken shim triggers an individual repair."""
    mox = CmdMox()
    mox._phase = Phase.REPLAY
    env = mox.environment
    env.shim_dir = tmp_path

    broken = {
        "first": tmp_path / "first-missing",
        "second": tmp_path / "second-missing",
    }
    for name, target in broken.items():
        shim_path = tmp_path / name
        shim_path.symlink_to(target)
        assert shim_path.is_symlink()
        assert not shim_path.exists()

    for name in broken:
        mox._ensure_shim_during_replay(name)

    assert shim_symlink_spy.calls == [
        (tmp_path, ("first",)),
        (tmp_path, ("second",)),
    ]


def test_ensure_shim_during_replay_rejects_non_symlink_collisions(
    tmp_path: Path,
) -> None:
    """A pre-existing file blocks shim repair to avoid data loss."""
    mox = CmdMox()
    mox._phase = Phase.REPLAY
    env = mox.environment
    env.shim_dir = tmp_path

    collision = tmp_path / "late"
    collision.write_text("collision")

    with pytest.raises(FileExistsError, match="already exists and is not a symlink"):
        mox._ensure_shim_during_replay("late")


def test_register_command_fails_when_path_exists() -> None:
    """register_command refuses to overwrite existing non-symlink files."""
    mox = CmdMox(verify_on_exit=False)
    mox.__enter__()
    mox.replay()

    env = mox.environment
    assert env is not None
    assert env.shim_dir is not None

    collision = env.shim_dir / "late"
    collision.write_text("collision")

    with pytest.raises(FileExistsError, match="already exists and is not a symlink"):
        mox.register_command("late")

    mox.__exit__(None, None, None)


def test_register_command_propagates_shim_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """register_command surfaces errors from shim creation helpers."""
    mox = CmdMox(verify_on_exit=False)
    mox.__enter__()
    mox.replay()

    def _boom(directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
        raise PermissionError

    monkeypatch.setattr(controller, "create_shim_symlinks", _boom)

    with pytest.raises(PermissionError):
        mox.register_command("late")

    mox.__exit__(None, None, None)


def test_register_command_skips_existing_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    """register_command avoids recreating an existing shim."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()

    env = mox.environment
    assert env is not None
    assert env.shim_dir is not None
    controller.create_shim_symlinks(env.shim_dir, ["again"])

    called = False

    def _fail(directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
        nonlocal called
        called = True
        return {name: directory / name for name in commands}

    monkeypatch.setattr(controller, "create_shim_symlinks", _fail)
    mox.register_command("again")

    assert not called

    mox.verify()


@pytest.mark.parametrize("call_replay_before_exception", [True, False])
def test_cmdmox_environment_cleanup_on_exception(
    *,
    call_replay_before_exception: bool,
) -> None:
    """Environment is cleaned even if an error occurs before or after replay."""
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

    # Use the public context-manager API to restore PATH and other state.
    # Calling the private _stop_server_and_exit_env helper would bypass
    # type checking, so tests rely on __exit__ instead.
    mox.__exit__(None, None, None)


def test_require_phase_mismatch() -> None:
    """_require_phase raises when current phase does not match."""
    mox = CmdMox()
    with pytest.raises(LifecycleError, match="not in 'replay' phase"):
        mox._require_phase(Phase.REPLAY, "replay")


def test_require_env_attrs(monkeypatch: pytest.MonkeyPatch) -> None:
    """_require_env_attrs reports missing EnvironmentManager attributes."""
    mox = CmdMox()
    mox.__enter__()
    monkeypatch.setattr(mox.environment, "shim_dir", None)
    monkeypatch.setattr(mox.environment, "socket_path", None)
    with pytest.raises(MissingEnvironmentError, match="shim_dir, socket_path"):
        mox._require_env_attrs("shim_dir", "socket_path")
    mox.__exit__(None, None, None)


def test_verify_missing_environment_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify() fails when environment attributes are missing."""
    mox = CmdMox(verify_on_exit=False)  # Disable auto-verify to avoid double error
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()
    mox.replay()

    monkeypatch.setattr(mox.environment, "shim_dir", None)
    monkeypatch.setattr(mox.environment, "socket_path", None)
    with pytest.raises(MissingEnvironmentError, match=r"shim_dir.*socket_path"):
        mox.verify()
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


def test_mock_and_spy_invocations(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Mock and spy commands record calls and verify correctly."""
    mox = CmdMox()
    mox.mock("hello").returns(stdout="hi")
    mox.spy("world").returns(stdout="earth")
    mox.__enter__()
    mox.replay()

    cmd_hello = Path(mox.environment.shim_dir) / "hello"
    cmd_world = Path(mox.environment.shim_dir) / "world"
    res1 = run([str(cmd_hello)])
    res2 = run([str(cmd_world)])

    mox.verify()

    assert res1.stdout.strip() == "hi"
    assert res2.stdout.strip() == "earth"
    assert len(mox.journal) == 2
    assert mox.mocks["hello"].invocations[0].command == "hello"
    assert mox.spies["world"].invocations[0].command == "world"


def test_invocation_order_multiple_calls(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Multiple calls are recorded in order."""
    mox = CmdMox()
    mox.mock("hello").returns(stdout="hi").times(2)
    mox.spy("world").returns(stdout="earth")
    mox.__enter__()
    mox.replay()

    cmd_hello = Path(mox.environment.shim_dir) / "hello"
    cmd_world = Path(mox.environment.shim_dir) / "world"
    run([str(cmd_hello)])
    run([str(cmd_world)])
    run([str(cmd_hello)])

    mox.verify()

    assert [inv.command for inv in mox.journal] == ["hello", "world", "hello"]
    assert len(mox.mocks["hello"].invocations) == 2
    assert len(mox.spies["world"].invocations) == 1


def test_context_manager_restores_env_on_exception(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Context manager restores environment even if an exception occurs."""

    class CustomError(Exception):
        """Exception used to trigger cleanup."""

    def run_with_error() -> None:
        with mox:
            mox.stub("boom").returns(stdout="oops")
            mox.replay()
            cmd_path = Path(mox.environment.shim_dir) / "boom"
            run([str(cmd_path)])
            raise CustomError

    original_env = os.environ.copy()
    mox = CmdMox()
    with pytest.raises(CustomError):
        run_with_error()

    assert os.environ == original_env


def test_context_manager_auto_verify(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Exiting the context automatically calls verify."""
    mox = CmdMox()
    mox.stub("hi").returns(stdout="hello")
    with mox:
        mox.replay()
        cmd_path = Path(mox.environment.shim_dir) / "hi"
        run([str(cmd_path)])

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
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Stub runs a dynamic handler when invoked."""
    mox = CmdMox()
    mox.stub(cmd).runs(handler)
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / cmd
    result = run([str(cmd_path)])

    mox.verify()

    assert result.stdout.strip() == expected


def test_invoke_handler_applies_env() -> None:
    """_invoke_handler uses temporary_env and propagates env in Response."""
    key = "SOME_VAR"
    mox = CmdMox()

    def handler(invocation: Invocation) -> Response:
        return Response(stdout=os.environ.get(key, ""))

    dbl = mox.stub("demo").with_env({key: "VAL"}).runs(handler)
    inv = Invocation(command="demo", args=[], stdin="", env={})

    assert key not in os.environ
    resp = mox._invoke_handler(dbl, inv)
    assert resp.stdout == "VAL"
    assert key not in os.environ
    assert resp.env == {key: "VAL"}


def test_prepare_passthrough_registers_pending_invocation() -> None:
    """_prepare_passthrough stores directives for the shim."""
    with CmdMox() as mox:
        spy = mox.spy("echo").passthrough()
        invocation = Invocation(command="echo", args=["hi"], stdin="", env={})
        response = mox._prepare_passthrough(spy, invocation)

        assert response.passthrough is not None
        directive = response.passthrough
        assert invocation.invocation_id == directive.invocation_id
        assert directive.lookup_path == mox.environment.original_environment.get(
            "PATH", os.environ.get("PATH", "")
        )
        assert mox._passthrough_coordinator.has_pending(directive.invocation_id)


def test_handle_passthrough_result_rejects_unknown_invocation() -> None:
    """Unexpected passthrough results should raise a clear RuntimeError."""
    with CmdMox() as mox:
        result = PassthroughResult(
            invocation_id="missing",
            stdout="",
            stderr="",
            exit_code=0,
        )
        with pytest.raises(RuntimeError, match="Unexpected passthrough result"):
            mox._handle_passthrough_result(result)

        spy = mox.spy("echo").passthrough()
        invocation = Invocation(command="echo", args=["hi"], stdin="", env={})
        prepared = mox._prepare_passthrough(spy, invocation)
        assert prepared.passthrough is not None
        assert mox._passthrough_coordinator.has_pending(
            prepared.passthrough.invocation_id
        )


def test_handle_passthrough_result_finalises_invocation() -> None:
    """_handle_passthrough_result records journal entries and clears state."""
    with CmdMox() as mox:
        spy = mox.spy("echo").passthrough()
        invocation = Invocation(command="echo", args=["hello"], stdin="", env={})
        response = mox._prepare_passthrough(spy, invocation)
        directive = response.passthrough
        assert directive is not None

        result = PassthroughResult(
            invocation_id=directive.invocation_id,
            stdout="out",
            stderr="",
            exit_code=7,
        )
        final = mox._handle_passthrough_result(result)

        assert final.stdout == "out"
        assert spy.invocations[0].stdout == "out"
        assert len(mox.journal) == 1
        recorded = mox.journal[0]
        assert recorded.exit_code == 7
        assert not mox._passthrough_coordinator.has_pending(directive.invocation_id)
        with pytest.raises(RuntimeError, match="Unexpected passthrough result"):
            mox._handle_passthrough_result(result)
