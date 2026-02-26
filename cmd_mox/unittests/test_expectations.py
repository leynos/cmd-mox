"""Unit tests for expectation matching and environment injection."""

from __future__ import annotations

import os
import typing as t

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
from cmd_mox.unittests._env_helpers import require_shim_dir

pytestmark = pytest.mark.requires_unix_sockets

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess
    from pathlib import Path


def test_mock_with_args_and_order(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Mocks require specific arguments and call order."""
    mox = CmdMox()
    mox.mock("first").with_args("a").returns(stdout="1").in_order().times(1)
    mox.mock("second").with_args("b").returns(stdout="2").in_order()
    mox.__enter__()
    mox.replay()

    path_first = require_shim_dir(mox.environment) / "first"
    path_second = require_shim_dir(mox.environment) / "second"
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

    path = require_shim_dir(mox.environment) / "foo"
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

    path = require_shim_dir(mox.environment) / "grep"
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

    path = require_shim_dir(mox.environment) / "env"
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

    path_first = require_shim_dir(mox.environment) / "first"
    path_second = require_shim_dir(mox.environment) / "second"

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

        path_first = require_shim_dir(mox.environment) / "first"
        path_second = require_shim_dir(mox.environment) / "second"
        path_third = require_shim_dir(mox.environment) / "third"

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

    paths = {name: require_shim_dir(mox.environment) / name for name in mox.mocks}

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

    path_first = require_shim_dir(mox.environment) / "first"
    path_second = require_shim_dir(mox.environment) / "second"

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


class TestStdinMatching:
    """Tests for Expectation stdin matching and mismatch explanation."""

    def test_literal_string_stdin_matches(self) -> None:
        """A literal string stdin matches when invocation.stdin is equal."""
        exp = Expectation("cmd").with_stdin("hello")
        inv = Invocation(command="cmd", args=[], stdin="hello", env={})
        assert exp.matches(inv) is True
        assert exp.explain_mismatch(inv) == "args, stdin, or env mismatch"

    def test_literal_string_stdin_mismatch(self) -> None:
        """A literal string stdin does not match a different value."""
        exp = Expectation("cmd").with_stdin("hello")
        inv = Invocation(command="cmd", args=[], stdin="world", env={})
        assert exp.matches(inv) is False
        reason = exp.explain_mismatch(inv)
        assert reason is not None
        assert "world" in reason

    def test_callable_stdin_matches(self) -> None:
        """A callable stdin predicate is invoked with invocation.stdin."""
        exp = Expectation("cmd").with_stdin(lambda s: s.startswith("ok"))
        inv = Invocation(command="cmd", args=[], stdin="ok-data", env={})
        assert exp.matches(inv) is True

    def test_callable_stdin_mismatch(self) -> None:
        """A callable stdin predicate that returns False causes mismatch."""
        exp = Expectation("cmd").with_stdin(lambda s: s == "expected")
        inv = Invocation(command="cmd", args=[], stdin="actual", env={})
        assert exp.matches(inv) is False
        reason = exp.explain_mismatch(inv)
        assert reason is not None
        assert "actual" in reason

    def test_callable_stdin_exception_returns_false(self) -> None:
        """A callable stdin predicate that raises is treated as non-match."""
        exp = Expectation("cmd").with_stdin(lambda s: 1 / 0)
        inv = Invocation(command="cmd", args=[], stdin="data", env={})
        assert exp.matches(inv) is False
        reason = exp.explain_mismatch(inv)
        assert reason is not None
        assert "raised" in reason

    def test_non_string_non_callable_stdin_does_not_match(self) -> None:
        """A non-string, non-callable stdin value is rejected.

        This test intentionally sets stdin to an invalid type via direct
        attribute mutation because the public with_stdin() API enforces
        type constraints.  It exercises the defensive fallback branch.
        """
        exp = Expectation("cmd")
        exp.stdin = 42  # type: ignore[assignment]
        inv = Invocation(command="cmd", args=[], stdin="hello", env={})
        assert exp.matches(inv) is False
        reason = exp.explain_mismatch(inv)
        assert reason is not None
        assert "not str or callable" in reason

    def test_none_stdin_always_matches(self) -> None:
        """When stdin expectation is None, any stdin value matches."""
        exp = Expectation("cmd")
        inv = Invocation(command="cmd", args=[], stdin="anything", env={})
        assert exp.matches(inv) is True


def test_validate_matchers_returns_false_when_missing() -> None:
    """_validate_matchers fails safely when matchers list is absent."""
    expectation = Expectation("cmd")
    assert not expectation._validate_matchers(["arg"])


def test_validate_matchers_rejects_length_mismatch() -> None:
    """_validate_matchers enforces one-to-one matcher/argument counts."""
    expectation = Expectation("cmd").with_matching_args(lambda _: True)
    assert not expectation._validate_matchers(["one", "two"])
