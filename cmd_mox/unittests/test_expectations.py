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
from cmd_mox.ipc import Invocation, Response

pytestmark = pytest.mark.requires_unix_sockets

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess


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


def test_with_env_rejects_non_string_keys() -> None:
    """with_env() should reject non-string keys and values."""
    expectation = Expectation("cmd")
    with pytest.raises(TypeError, match="name must be str"):
        expectation.with_env({42: "value"})  # type: ignore[arg-type]


def test_with_env_rejects_empty_key() -> None:
    """with_env() should reject empty environment variable names."""
    expectation = Expectation("cmd")
    with pytest.raises(ValueError, match="cannot be empty"):
        expectation.with_env({"": "value"})


def test_with_env_rejects_non_string_values() -> None:
    """with_env() should reject non-string values."""
    expectation = Expectation("cmd")
    with pytest.raises(TypeError, match="value must be str"):
        expectation.with_env({"VAR": 7})  # type: ignore[arg-type]


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


def test_multiple_any_order_expectations_do_not_enforce_order(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Unordered expectations remain unordered when combined."""
    with CmdMox() as mox:
        mox.mock("first").returns(stdout="1").any_order()
        mox.mock("second").returns(stdout="2").any_order()
        mox.mock("third").returns(stdout="3").any_order()
        mox.replay()

        path_first = Path(mox.environment.shim_dir) / "first"
        path_second = Path(mox.environment.shim_dir) / "second"
        path_third = Path(mox.environment.shim_dir) / "third"

        # Call expectations in a different order than defined
        run([str(path_third)], shell=False)
        run([str(path_first)], shell=False)
        run([str(path_second)], shell=False)

        mox.verify()

        assert len(mox.mocks["first"].invocations) == 1
        assert len(mox.mocks["second"].invocations) == 1
        assert len(mox.mocks["third"].invocations) == 1


def _test_expectation_failure_helper(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
    mock_configurator: t.Callable[[CmdMox], None],
    execution_strategy: t.Callable[
        [t.Callable[..., subprocess.CompletedProcess[str]], dict[str, Path]], None
    ],
    expected_exception: type[Exception] = UnfulfilledExpectationError,
) -> None:
    """Execute a scenario expected to fail."""
    mox = CmdMox()
    mock_configurator(mox)
    mox.__enter__()
    mox.replay()

    paths = {name: Path(mox.environment.shim_dir) / name for name in mox.mocks}

    execution_strategy(run, paths)

    with pytest.raises(expected_exception):
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


@pytest.mark.parametrize(
    (
        "mock_configurator",
        "execution_strategy",
        "expected_exception",
    ),
    [
        pytest.param(
            lambda mox: (
                mox.mock("first").returns(stdout="1").in_order(),
                mox.mock("second").returns(stdout="2").in_order(),
            ),
            lambda run, paths: (
                run([str(paths["second"])], shell=False),
                run([str(paths["first"])], shell=False),
            ),
            UnexpectedCommandError,
            id="order-validation",
        ),
        pytest.param(
            lambda mox: (
                mox.mock("first").returns(stdout="1").times(2),
                mox.mock("second").returns(stdout="2").times_called(2),
            ),
            lambda run, paths: (
                run([str(paths["first"])], shell=False),
                run([str(paths["second"])], shell=False),
            ),
            UnfulfilledExpectationError,
            id="count-validation",
        ),
        pytest.param(
            lambda mox: (mox.mock("first").returns(stdout="1").any_order().times(2),),
            lambda run, paths: (run([str(paths["first"])], shell=False),),
            UnfulfilledExpectationError,
            id="any_order_call_count_fail",
        ),
        pytest.param(
            lambda mox: (
                mox.mock("first").returns(stdout="1").any_order().times_called(2),
            ),
            lambda run, paths: (run([str(paths["first"])], shell=False),),
            UnfulfilledExpectationError,
            id="any_order_call_count_fail_times_called",
        ),
        pytest.param(
            lambda mox: (mox.mock("first").returns(stdout="1").any_order().times(1),),
            lambda run, paths: (
                run([str(paths["first"])], shell=False),
                run([str(paths["first"])], shell=False),
            ),
            UnexpectedCommandError,
            id="any_order_call_count_excess_times",
        ),
        pytest.param(
            lambda mox: (
                mox.mock("first").returns(stdout="1").any_order().times_called(1),
            ),
            lambda run, paths: (
                run([str(paths["first"])], shell=False),
                run([str(paths["first"])], shell=False),
            ),
            UnexpectedCommandError,
            id="any_order_call_count_excess_times_called",
        ),
    ],
)
def test_expectation_failures(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
    mock_configurator: t.Callable[[CmdMox], None],
    execution_strategy: t.Callable[
        [t.Callable[..., subprocess.CompletedProcess[str]], dict[str, Path]], None
    ],
    expected_exception: type[Exception],
) -> None:
    """Verify expectation scenarios that should fail verification."""
    _test_expectation_failure_helper(
        run, mock_configurator, execution_strategy, expected_exception
    )
