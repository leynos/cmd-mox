"""Unit tests for expectation matching and environment injection."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cmd_mox import CmdMox, Regex, UnexpectedCommandError
from cmd_mox.ipc import Invocation, Response


def test_mock_with_args_and_order() -> None:
    """Mocks require specific arguments and call order."""
    mox = CmdMox()
    mox.mock("first").with_args("a").returns(stdout="1").in_order().times(1)
    mox.mock("second").with_args("b").returns(stdout="2").in_order()
    mox.__enter__()
    mox.replay()

    path_first = Path(mox.environment.shim_dir) / "first"
    path_second = Path(mox.environment.shim_dir) / "second"
    subprocess.run(  # noqa: S603
        [str(path_first), "a"], capture_output=True, text=True, check=True, shell=False
    )
    subprocess.run(  # noqa: S603
        [str(path_second), "b"], capture_output=True, text=True, check=True, shell=False
    )

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
    subprocess.run(  # noqa: S603
        [str(path), "baz"], capture_output=True, text=True, check=True, shell=False
    )

    with pytest.raises(UnexpectedCommandError):
        mox.verify()


def test_with_matching_args_and_stdin() -> None:
    """Regular expressions and stdin matching are supported."""
    mox = CmdMox()
    mox.mock("grep").with_matching_args(Regex(r"foo=\d+")).with_stdin("data")
    mox.__enter__()
    mox.replay()

    path = Path(mox.environment.shim_dir) / "grep"
    subprocess.run(  # noqa: S603
        [str(path), "foo=123"],
        input="data",
        text=True,
        capture_output=True,
        check=True,
        shell=False,
    )

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
    result = subprocess.run(  # noqa: S603
        [str(path)], capture_output=True, text=True, check=True, shell=False
    )
    mox.verify()

    assert result.stdout.strip() == "WORLD"
