"""Unit tests for expectation matching and environment injection."""

from __future__ import annotations

import os
from pathlib import Path

# These tests invoke shim binaries with `shell=False` so the command
# strings are not interpreted by the shell. The paths come from the
# environment manager and are not user controlled, which avoids
# subprocess command injection issues.
import pytest

from cmd_mox import CmdMox, Regex, UnexpectedCommandError
from cmd_mox.ipc import Invocation, Response
from tests.helpers import run_cmd


def test_mock_with_args_and_order() -> None:
    """Mocks require specific arguments and call order."""
    mox = CmdMox()
    mox.mock("first").with_args("a").returns(stdout="1").in_order().times(1)
    mox.mock("second").with_args("b").returns(stdout="2").in_order()
    mox.__enter__()
    mox.replay()

    path_first = Path(mox.environment.shim_dir) / "first"
    path_second = Path(mox.environment.shim_dir) / "second"
    run_cmd([path_first, "a"])
    run_cmd([path_second, "b"])

    mox.verify()

    assert len(mox.mocks["first"].invocations) == 1
    assert len(mox.mocks["second"].invocations) == 1


def test_mock_argument_mismatch() -> None:
    """Verification fails when arguments differ from expectation."""
    mox = CmdMox()
    mox.mock("foo").with_args("bar")
    mox.__enter__()
    mox.replay()

    path = Path(mox.environment.shim_dir) / "foo"
    run_cmd([path, "baz"])

    with pytest.raises(UnexpectedCommandError):
        mox.verify()


def test_with_matching_args_and_stdin() -> None:
    """Regular expressions and stdin matching are supported."""
    mox = CmdMox()
    mox.mock("grep").with_matching_args(Regex(r"foo=\d+")).with_stdin("data")
    mox.__enter__()
    mox.replay()

    path = Path(mox.environment.shim_dir) / "grep"
    run_cmd([path, "foo=123"], input="data")

    mox.verify()


def test_with_env_injection() -> None:
    """Environment variables provided via with_env() are applied."""
    mox = CmdMox()

    def handler(inv: Invocation) -> Response:
        return Response(stdout=os.environ.get("HELLO", ""))

    mox.stub("env").with_env({"HELLO": "WORLD"}).runs(handler)
    mox.__enter__()
    mox.replay()

    path = Path(mox.environment.shim_dir) / "env"
    result = run_cmd([path])
    mox.verify()

    assert result.stdout.strip() == "WORLD"
