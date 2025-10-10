"""Tests for spy assertion helpers."""

import dataclasses as dc
import os
import subprocess
import typing as t
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox, CommandDouble
from cmd_mox.ipc import Invocation

pytestmark = pytest.mark.requires_unix_sockets


@dc.dataclass
class SpyCommandConfig:
    """Configuration for spy command execution."""

    cmd_args: list[str] | None = None
    stdin_input: str | None = None
    env: dict[str, str] | None = None
    cmd_name: str = "hi"
    stdout_return: str = "hello"


@dc.dataclass
class AssertionTestConfig:
    """Configuration for assertion failure tests."""

    method_name: str
    args: tuple[object, ...] = ()
    kwargs: dict[str, object] = dc.field(default_factory=dict)
    expected_message: str | None = None


class TestSpyAssertions:
    """Tests covering the spy assertion helper API."""

    # ------------------------------------------------------------------
    def _create_spy_and_run_command(
        self,
        run: t.Callable[..., subprocess.CompletedProcess[str]],
        config: SpyCommandConfig | None = None,
    ) -> tuple[CmdMox, CommandDouble]:
        """Return a ``(mox, spy)`` pair after running a command.

        This helper performs the full mox lifecycle and executes the command
        with the provided arguments, stdin, and environment.
        """
        if config is None:
            config = SpyCommandConfig()

        mox = CmdMox()
        spy = mox.spy(config.cmd_name).returns(stdout=config.stdout_return)
        mox.__enter__()
        mox.replay()

        full_env = dict(os.environ, **(config.env or {}))
        cmd_path = Path(mox.environment.shim_dir) / config.cmd_name
        run(
            [str(cmd_path), *(config.cmd_args or [])],
            env=full_env,
            input=config.stdin_input,
        )

        mox.verify()
        return mox, spy

    # ------------------------------------------------------------------
    def _create_spy_with_invocation(
        self,
        cmd: str,
        args: list[str],
        stdin: str,
        env: dict[str, str],
    ) -> CommandDouble:
        """Create a spy pre-populated with a single invocation."""
        mox = CmdMox()
        spy = mox.spy(cmd)
        invocation = Invocation(cmd, args, stdin, env)
        spy.invocations.append(invocation)
        return spy

    # ------------------------------------------------------------------
    def _assert_raises_assertion_error(
        self,
        spy: CommandDouble,
        config: AssertionTestConfig,
    ) -> None:
        """Invoke ``spy`` method and assert it raises ``AssertionError``."""
        method = getattr(spy, config.method_name)
        with pytest.raises(AssertionError) as exc:
            method(*config.args, **config.kwargs)
        if config.expected_message is not None:
            assert str(exc.value) == config.expected_message

    # ------------------------------------------------------------------
    def test_spy_assert_called_and_called_with(
        self, run: t.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """Spy exposes assert helpers mirroring unittest.mock."""
        _, spy = self._create_spy_and_run_command(
            run, SpyCommandConfig(cmd_args=["foo", "bar"], stdin_input="stdin")
        )
        spy.assert_called()
        spy.assert_called_with("foo", "bar", stdin="stdin")

    # ------------------------------------------------------------------
    def test_spy_assert_called_with_env(
        self, run: t.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """assert_called_with validates the environment mapping."""
        _, spy = self._create_spy_and_run_command(
            run,
            SpyCommandConfig(
                cmd_args=["foo"], stdin_input="stdin", env={"MYVAR": "VALUE"}
            ),
        )
        actual_env = spy.invocations[0].env
        spy.assert_called_with("foo", stdin="stdin", env=actual_env)

        bad_env = dict(actual_env, MYVAR="DIFFERENT")
        self._assert_raises_assertion_error(
            spy,
            AssertionTestConfig(
                method_name="assert_called_with",
                args=("foo",),
                kwargs={"stdin": "stdin", "env": bad_env},
                expected_message=(
                    f"'hi' called with env {actual_env!r}, expected {bad_env!r}"
                ),
            ),
        )

    # ------------------------------------------------------------------
    def test_spy_assert_called_raises_when_never_called(self) -> None:
        """assert_called raises when the spy was never invoked."""
        mox = CmdMox()
        spy = mox.spy("hi")
        mox.__enter__()
        mox.replay()
        mox.verify()

        self._assert_raises_assertion_error(
            spy, AssertionTestConfig(method_name="assert_called")
        )
        spy.assert_not_called()

    # ------------------------------------------------------------------
    def test_spy_assert_not_called_raises_when_called(self) -> None:
        """assert_not_called raises if the spy was invoked."""
        spy = self._create_spy_with_invocation("hi", [], "", {})
        self._assert_raises_assertion_error(
            spy,
            AssertionTestConfig(
                method_name="assert_not_called",
                expected_message=(
                    "Expected 'hi' to be uncalled but it was called 1 time(s); "
                    "last args=[], stdin='', env={}"
                ),
            ),
        )

    # ------------------------------------------------------------------
    def test_spy_assert_called_with_partial_args(
        self, run: t.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """assert_called_with fails for subset or superset of args."""
        _, spy = self._create_spy_and_run_command(
            run, SpyCommandConfig(cmd_args=["foo", "bar"])
        )
        self._assert_raises_assertion_error(
            spy,
            AssertionTestConfig(
                method_name="assert_called_with",
                args=("foo",),
                expected_message=(
                    "'hi' called with args ('foo', 'bar'), expected ('foo',)"
                ),
            ),
        )
        self._assert_raises_assertion_error(
            spy,
            AssertionTestConfig(
                method_name="assert_called_with",
                args=("foo", "bar", "baz"),
                expected_message=(
                    "'hi' called with args ('foo', 'bar'), expected "
                    "('foo', 'bar', 'baz')"
                ),
            ),
        )

    # ------------------------------------------------------------------
    def test_validate_spy_usage_only_allows_spies(self) -> None:
        """_validate_spy_usage permits spies and rejects other doubles."""
        mox = CmdMox()
        spy = mox.spy("spy_cmd")
        spy._validate_spy_usage("assert_called_with")
        mock = mox.mock("mock_cmd")
        self._assert_raises_assertion_error(
            mock,
            AssertionTestConfig(
                method_name="_validate_spy_usage",
                args=("assert_called_with",),
                expected_message="assert_called_with() is only valid for spies",
            ),
        )

    # ------------------------------------------------------------------
    def test_get_last_invocation_behaviour(self) -> None:
        """_get_last_invocation returns the last call and errors when absent."""
        mox = CmdMox()
        spy = mox.spy("hi")
        self._assert_raises_assertion_error(
            spy,
            AssertionTestConfig(
                method_name="_get_last_invocation",
                expected_message="Expected 'hi' to be called but it was never called",
            ),
        )
        invocation = Invocation("hi", ["foo"], "", {})
        spy.invocations.append(invocation)
        assert spy._get_last_invocation() is invocation

    # ------------------------------------------------------------------
    ASSERTION_FAILURE_SCENARIOS: t.ClassVar[list[dict[str, t.Any]]] = [
        {
            "name": "spy_assert_called_with_mismatched_args",
            "setup": lambda self, run: self._create_spy_and_run_command(
                run, SpyCommandConfig(cmd_args=["actual"])
            )[1],
            "method": "assert_called_with",
            "args": lambda spy: ("expected",),
            "kwargs": lambda spy: {},
            "expected_message": (
                "'hi' called with args ('actual',), expected ('expected',)"
            ),
        },
        {
            "name": "spy_assert_called_with_mismatched_stdin",
            "setup": lambda self, run: self._create_spy_and_run_command(
                run, SpyCommandConfig(stdin_input="actual")
            )[1],
            "method": "assert_called_with",
            "args": lambda spy: (),
            "kwargs": lambda spy: {"stdin": "expected"},
            "expected_message": (
                "'hi' called with stdin 'actual', expected 'expected'"
            ),
        },
        {
            "name": "validate_arguments_raises_on_mismatch",
            "setup": lambda self, run: self._create_spy_with_invocation(
                "hi", ["foo"], "", {}
            ),
            "method": "_validate_arguments",
            "args": lambda spy: (spy.invocations[0], ("bar",)),
            "kwargs": lambda spy: {},
            "expected_message": ("'hi' called with args ('foo',), expected ('bar',)"),
        },
        {
            "name": "validate_stdin_raises_on_mismatch",
            "setup": lambda self, run: self._create_spy_with_invocation(
                "hi", [], "actual", {}
            ),
            "method": "_validate_stdin",
            "args": lambda spy: (spy.invocations[0], "expected"),
            "kwargs": lambda spy: {},
            "expected_message": (
                "'hi' called with stdin 'actual', expected 'expected'"
            ),
        },
        {
            "name": "validate_environment_raises_on_mismatch",
            "setup": lambda self, run: self._create_spy_with_invocation(
                "hi", [], "", {"A": "1"}
            ),
            "method": "_validate_environment",
            "args": lambda spy: (spy.invocations[0], {"B": "2"}),
            "kwargs": lambda spy: {},
            "expected_message": (
                "'hi' called with env {'A': '1'}, expected {'B': '2'}"
            ),
        },
        {
            "name": "assert_equal_raises_on_mismatch",
            "setup": lambda self, run: self._create_spy_with_invocation(
                "hi", [], "", {}
            ),
            "method": "_assert_equal",
            "args": lambda spy: ("label", "actual", "expected"),
            "kwargs": lambda spy: {},
            "expected_message": (
                "'hi' called with label 'actual', expected 'expected'"
            ),
        },
    ]

    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        "scenario",
        ASSERTION_FAILURE_SCENARIOS,
        ids=lambda s: s["name"],
    )
    def test_spy_assertion_failures(
        self,
        run: t.Callable[..., subprocess.CompletedProcess[str]],
        scenario: dict[str, t.Any],
    ) -> None:
        """Check that mismatches raise ``AssertionError`` with expected message."""
        spy = scenario["setup"](self, run)
        args = scenario["args"](spy) if callable(scenario["args"]) else scenario["args"]
        kwargs = (
            scenario["kwargs"](spy)
            if callable(scenario["kwargs"])
            else scenario["kwargs"]
        )
        self._assert_raises_assertion_error(
            spy,
            AssertionTestConfig(
                method_name=scenario["method"],
                args=args,
                kwargs=kwargs,
                expected_message=scenario["expected_message"],
            ),
        )

        post_validations = {
            "_validate_arguments": lambda s: s._validate_arguments(
                s.invocations[0], tuple(s.invocations[0].args)
            ),
            "_validate_stdin": lambda s: s._validate_stdin(
                s.invocations[0], s.invocations[0].stdin
            ),
            "_validate_environment": lambda s: s._validate_environment(
                s.invocations[0], s.invocations[0].env
            ),
            "_assert_equal": lambda s: s._assert_equal("label", "expected", "expected"),
        }
        post = post_validations.get(scenario["method"])
        if post is not None:
            post(spy)

    # ------------------------------------------------------------------
    def test_assert_equal_passes_on_match(self) -> None:
        """_assert_equal returns quietly when values match."""
        spy = self._create_spy_with_invocation("hi", [], "", {})
        assert spy._assert_equal("label", "expected", "expected") is None
