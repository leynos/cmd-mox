"""Unit tests for expectation matching and environment injection."""

from __future__ import annotations

import os
import typing as t
from pathlib import Path

# These tests invoke shim binaries with `shell=False` so the command
# strings are not interpreted by the shell. The paths come from the
# environment manager and are not user controlled, which avoids
# subprocess command injection issues.
import pytest

from cmd_mox import (
    CmdMox,
    Regex,
    UnexpectedCommandError,
    UnfulfilledExpectationError,
)
from cmd_mox.expectations import Expectation

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess
from cmd_mox.ipc import Invocation, Response


def test_mock_with_args_and_order(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Mocks require specific arguments and call order."""
    mox = CmdMox()
    mox.mock("first").with_args("a").returns(stdout="1").in_order().times(1)
    mox.mock("second").with_args("b").returns(stdout="2").in_order()
    mox.__enter__()
    mox.replay()

    path_first = Path(mox.environment.shim_dir) / "first"
    path_second = Path(mox.environment.shim_dir) / "second"
    run([str(path_first), "a"], shell=False)
    run([str(path_second), "b"], shell=False)

    mox.verify()

    assert len(mox.mocks["first"].invocations) == 1
    assert len(mox.mocks["second"].invocations) == 1


def test_mock_argument_mismatch(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Verification fails when arguments differ from expectation."""
    mox = CmdMox()
    mox.mock("foo").with_args("bar")
    mox.__enter__()
    mox.replay()

    path = Path(mox.environment.shim_dir) / "foo"
    run([str(path), "baz"], shell=False)

    with pytest.raises(UnexpectedCommandError):
        mox.verify()


def test_with_matching_args_and_stdin(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Regular expressions and stdin matching are supported."""
    mox = CmdMox()
    mox.mock("grep").with_matching_args(Regex(r"foo=\d+")).with_stdin("data")
    mox.__enter__()
    mox.replay()

    path = Path(mox.environment.shim_dir) / "grep"
    run(
        [str(path), "foo=123"],
        input="data",
        shell=False,
    )

    mox.verify()


def test_with_env_injection(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Environment variables provided via with_env() are applied."""
    mox = CmdMox()

    def handler(inv: Invocation) -> Response:
        return Response(stdout=os.environ.get("HELLO", ""))

    mox.stub("env").with_env({"HELLO": "WORLD"}).runs(handler)
    mox.__enter__()
    mox.replay()

    path = Path(mox.environment.shim_dir) / "env"
    result = run([str(path)], shell=False)
    mox.verify()

    assert result.stdout.strip() == "WORLD"


def test_any_order_expectations_allow_flexible_sequence(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Expectations marked any_order() should not enforce call ordering."""
    mox = CmdMox()
    mox.mock("first").returns(stdout="1").in_order()
    mox.mock("second").returns(stdout="2").any_order()
    mox.__enter__()
    mox.replay()

    path_first = Path(mox.environment.shim_dir) / "first"
    path_second = Path(mox.environment.shim_dir) / "second"

    # Invoke the any_order expectation before the ordered one
    run([str(path_second)], shell=False)
    run([str(path_first)], shell=False)

    mox.verify()


def test_in_order_expectations_fail_on_out_of_order(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """in_order() expectations should fail when invoked out of sequence."""
    mox = CmdMox()
    mox.mock("first").returns(stdout="1").in_order()
    mox.mock("second").returns(stdout="2").in_order()
    mox.__enter__()
    mox.replay()

    path_first = Path(mox.environment.shim_dir) / "first"
    path_second = Path(mox.environment.shim_dir) / "second"

    # Call "second" before "first", then attempt verification
    run([str(path_second)], shell=False)
    run([str(path_first)], shell=False)

    with pytest.raises(UnfulfilledExpectationError):
        mox.verify()


def test_expectation_times_alias(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Expectation.times() and times_called() behave interchangeably."""
    exp = Expectation("foo").times(3)
    assert exp.count == 3

    mox = CmdMox()
    mox.mock("first").returns(stdout="1").times(2)
    mox.mock("second").returns(stdout="2").times_called(2)
    mox.__enter__()
    mox.replay()

    path_first = Path(mox.environment.shim_dir) / "first"
    path_second = Path(mox.environment.shim_dir) / "second"

    run([str(path_first)], shell=False)
    run([str(path_first)], shell=False)
    run([str(path_second)], shell=False)
    run([str(path_second)], shell=False)

    mox.verify()

    assert len(mox.mocks["first"].invocations) == 2
    assert len(mox.mocks["second"].invocations) == 2


def test_expectation_times_alias_mismatch_fails(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """times() and times_called() should both enforce invocation counts."""
    mox = CmdMox()
    mox.mock("first").returns(stdout="1").times(2)
    mox.mock("second").returns(stdout="2").times_called(2)
    mox.__enter__()
    mox.replay()

    path_first = Path(mox.environment.shim_dir) / "first"
    path_second = Path(mox.environment.shim_dir) / "second"

    # Each mock is invoked only once, below the expected count
    run([str(path_first)], shell=False)
    run([str(path_second)], shell=False)

    with pytest.raises(UnfulfilledExpectationError):
        mox.verify()
