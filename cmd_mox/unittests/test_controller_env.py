"""Unit tests for :mod:`cmd_mox.controller` - environment variable handling."""

from __future__ import annotations

import os
import typing as t
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.ipc import Invocation, Response

pytestmark = pytest.mark.requires_unix_sockets

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    import subprocess


def test_mock_with_env_static_response(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Mocks with env overrides should verify when caller omits variables."""
    key = "EXPECT_ENV"
    with CmdMox() as mox:
        mox.mock("envcmd").with_env({key: "VALUE"}).returns(stdout="ok")
        mox.replay()

        cmd_path = Path(mox.environment.shim_dir) / "envcmd"
        result = run([str(cmd_path)])

        assert result.stdout.strip() == "ok"

        mox.verify()


def test_mock_with_env_rejects_non_string_key() -> None:
    """with_env() should reject non-string keys when used via the DSL."""
    mox = CmdMox()

    with pytest.raises(TypeError, match="Environment variable name must be str"):
        mox.mock("envcmd").with_env({42: "value"})  # type: ignore[arg-type]


def test_invoke_handler_preserves_handler_env_override() -> None:
    """Handler-provided env entries should not be clobbered by expectation env."""

    def handler(inv: Invocation) -> Response:
        response = Response(stdout="ok")
        response.env["EXPECT_ENV"] = "handler"
        return response

    mox = CmdMox()
    double = mox.stub("envcmd").with_env({"EXPECT_ENV": "expected"}).runs(handler)
    invocation = Invocation(command="envcmd", args=[], stdin="", env={})

    resp = mox._invoke_handler(double, invocation)

    assert invocation.env["EXPECT_ENV"] == "expected"
    assert resp.env["EXPECT_ENV"] == "handler"


def test_invoke_handler_rejects_conflicting_env() -> None:
    """Invocations providing conflicting env should raise an error."""
    key = "EXPECT_ENV"
    mox = CmdMox()
    double = mox.mock("envcmd").with_env({key: "EXPECTED"}).returns(stdout="ok")
    invocation = Invocation(command="envcmd", args=[], stdin="", env={key: "DIFF"})

    with pytest.raises(UnexpectedCommandError, match="conflicting environment"):
        mox._invoke_handler(double, invocation)


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
    assert inv.env[key] == "VAL"


def test_invoke_handler_applies_env_to_static_response() -> None:
    """Environment overrides apply when returning a canned response."""
    key = "STATIC_VAR"
    mox = CmdMox()
    dbl = mox.stub("demo").with_env({key: "VAL"}).returns(stdout="ok")
    inv = Invocation(command="demo", args=[], stdin="", env={})

    assert key not in os.environ
    resp = mox._invoke_handler(dbl, inv)
    assert resp.stdout == "ok"
    assert resp.env == {key: "VAL"}
    assert key not in os.environ
    assert inv.env[key] == "VAL"


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
