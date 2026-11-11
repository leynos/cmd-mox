"""Tests for spy assertion helpers."""

import collections.abc as cabc
import dataclasses as dc
import operator
import os
import subprocess
import typing as typ
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox, CommandDouble
from cmd_mox.domain import mask_env_mapping
from cmd_mox.errors import (
    UnexpectedCommandError,
    UnfulfilledExpectationError,
    VerificationError,
)
from cmd_mox.ipc import Invocation
from cmd_mox.unittests._env_helpers import require_shim_dir
from tests.helpers.fixtures import write_minimal_replay_fixture

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
        run: cabc.Callable[..., subprocess.CompletedProcess[str]],
        config: SpyCommandConfig | None = None,
    ) -> tuple[CmdMox, CommandDouble]:
        """Return a ``(mox, spy)`` pair after running a command.

        This helper performs the full mox lifecycle and executes the command
        with the provided arguments, stdin, and environment.

        Returns
        -------
        tuple[CmdMox, CommandDouble]
            The configured controller and its executed spy.
        """
        if config is None:
            config = SpyCommandConfig()

        mox = CmdMox()
        spy = mox.spy(config.cmd_name).returns(stdout=config.stdout_return)
        mox.__enter__()
        mox.replay()

        full_env = dict(os.environ, **(config.env or {}))
        cmd_path = require_shim_dir(mox.environment) / config.cmd_name
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
        """Create a spy pre-populated with a single invocation.

        Returns
        -------
        CommandDouble
            The spy containing the supplied invocation.
        """
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
        self, run: cabc.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """Spy exposes assert helpers mirroring unittest.mock."""
        _, spy = self._create_spy_and_run_command(
            run, SpyCommandConfig(cmd_args=["foo", "bar"], stdin_input="stdin")
        )
        spy.assert_called()
        spy.assert_called_with("foo", "bar", stdin="stdin")

    # ------------------------------------------------------------------
    def test_spy_assert_called_with_env(
        self, run: cabc.Callable[..., subprocess.CompletedProcess[str]]
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
        # The recorded environment inherits the test runner's variables, some
        # of which look sensitive, so the diagnostic renders masked copies.
        self._assert_raises_assertion_error(
            spy,
            AssertionTestConfig(
                method_name="assert_called_with",
                args=("foo",),
                kwargs={"stdin": "stdin", "env": bad_env},
                expected_message=(
                    f"'hi' called with env {mask_env_mapping(actual_env)!r}, "
                    f"expected {mask_env_mapping(bad_env)!r}"
                ),
            ),
        )

    # ------------------------------------------------------------------
    def test_spy_assert_not_called_masks_sensitive_env_values(self) -> None:
        """assert_not_called masks secret values in the recorded environment."""
        recorded_value = "leaked-secret"
        spy = self._create_spy_with_invocation(
            "hi", [], "", {"DB_PWD": recorded_value, "LOG_LEVEL": "debug"}
        )

        with pytest.raises(AssertionError) as exc:
            spy.assert_not_called()

        message = str(exc.value)
        assert recorded_value not in message, "Value leaked into assert_not_called"
        assert "DB_PWD': '***'" in message, "Sensitive key was not masked"
        assert "LOG_LEVEL': 'debug'" in message, "Benign value disappeared"
        assert spy.invocations[0].env == {
            "DB_PWD": recorded_value,
            "LOG_LEVEL": "debug",
        }, "assert_not_called mutated the recorded invocation"

    # ------------------------------------------------------------------
    def test_spy_assert_called_with_masks_sensitive_env_values(self) -> None:
        """assert_called_with masks secrets in both compared mappings."""
        recorded_value = "actual-secret"
        provided_value = "expected-secret"
        spy = self._create_spy_with_invocation(
            "hi", [], "", {"DB_PWD": recorded_value, "LOG_LEVEL": "debug"}
        )

        with pytest.raises(AssertionError) as exc:
            spy.assert_called_with(env={"DB_PWD": provided_value, "LOG_LEVEL": "info"})

        message = str(exc.value)
        assert recorded_value not in message, "Recorded value leaked into the message"
        assert provided_value not in message, "Expected value leaked into the message"
        assert message.count("DB_PWD': '***'") == 2, "Both mappings should mask DB_PWD"
        assert "LOG_LEVEL': 'debug'" in message, "Actual benign value disappeared"
        assert "LOG_LEVEL': 'info'" in message, "Expected benign value disappeared"
        assert spy.invocations[0].env == {
            "DB_PWD": recorded_value,
            "LOG_LEVEL": "debug",
        }, "assert_called_with mutated the recorded invocation"

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
        self, run: cabc.Callable[..., subprocess.CompletedProcess[str]]
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
    def _run_spy_with_explicit_count(
        self,
        run: cabc.Callable[..., subprocess.CompletedProcess[str]],
        count_method: str,
        expected_count: int,
        actual_calls: int,
    ) -> tuple[CmdMox, CommandDouble]:
        """Configure a spy count and invoke the shim *actual_calls* times.

        Returns
        -------
        tuple[CmdMox, CommandDouble]
            The controller awaiting verification and its spy.
        """
        mox = CmdMox()
        spy = mox.spy("hi").returns(stdout="hello")
        getattr(spy, count_method)(expected_count)
        mox.__enter__()
        mox.replay()
        path = require_shim_dir(mox.environment) / "hi"
        for _ in range(actual_calls):
            run([str(path)], shell=False)
        return mox, spy

    # ------------------------------------------------------------------
    @pytest.mark.parametrize("count_method", ["times", "times_called"])
    def test_spy_explicit_count_matches(
        self,
        run: cabc.Callable[..., subprocess.CompletedProcess[str]],
        count_method: str,
    ) -> None:
        """An explicit spy count is satisfied by the matching call count."""
        mox, spy = self._run_spy_with_explicit_count(run, count_method, 2, 2)
        mox.verify()
        assert spy.call_count == 2

    # ------------------------------------------------------------------
    @pytest.mark.parametrize("count_method", ["times", "times_called"])
    def test_spy_explicit_count_under_count_fails(
        self,
        run: cabc.Callable[..., subprocess.CompletedProcess[str]],
        count_method: str,
    ) -> None:
        """Fewer spy calls than the explicit count fail verification."""
        mox, _ = self._run_spy_with_explicit_count(run, count_method, 2, 1)
        with pytest.raises(UnfulfilledExpectationError) as exc:
            mox.verify()
        message = str(exc.value)
        assert "Unfulfilled expectation." in message
        assert "expected calls=2" in message

    # ------------------------------------------------------------------
    @pytest.mark.parametrize("count_method", ["times", "times_called"])
    def test_spy_explicit_count_over_count_fails(
        self,
        run: cabc.Callable[..., subprocess.CompletedProcess[str]],
        count_method: str,
    ) -> None:
        """More spy calls than the explicit count fail verification."""
        mox, _ = self._run_spy_with_explicit_count(run, count_method, 1, 2)
        with pytest.raises(UnexpectedCommandError) as exc:
            mox.verify()
        message = str(exc.value)
        assert "Unexpected additional invocation." in message
        assert "expected calls=1" in message

    # ------------------------------------------------------------------
    @pytest.mark.parametrize(
        ("expected_count", "actual_calls", "error_type"),
        [
            pytest.param(1, 0, UnfulfilledExpectationError, id="one-zero-calls"),
            pytest.param(0, 1, UnexpectedCommandError, id="zero-one-call"),
        ],
    )
    def test_spy_explicit_boundary_counts(
        self,
        run: cabc.Callable[..., subprocess.CompletedProcess[str]],
        expected_count: int,
        actual_calls: int,
        error_type: type[VerificationError],
    ) -> None:
        """Explicit ``times(1)`` and ``times(0)`` are enforced at the boundary."""
        mox, _ = self._run_spy_with_explicit_count(
            run, "times", expected_count, actual_calls
        )
        with pytest.raises(error_type):
            mox.verify()

    # ------------------------------------------------------------------
    @pytest.mark.parametrize("actual_calls", [0, 3])
    def test_unconfigured_spy_count_is_observational(
        self,
        run: cabc.Callable[..., subprocess.CompletedProcess[str]],
        actual_calls: int,
    ) -> None:
        """A spy without an explicit count never fails verification."""
        mox = CmdMox()
        spy = mox.spy("hi").returns(stdout="hello")
        mox.__enter__()
        mox.replay()
        path = require_shim_dir(mox.environment) / "hi"
        for _ in range(actual_calls):
            run([str(path)], shell=False)
        mox.verify()
        assert spy.call_count == actual_calls

    # ------------------------------------------------------------------
    def test_spy_handler_pattern_remains_observational(
        self, run: cabc.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """The usage-guide handler pattern records calls without a count."""
        mox = CmdMox()
        seen: list[list[str]] = []

        def handler(invocation: Invocation) -> tuple[str, str, int]:
            """Record the arguments and return a canned success."""
            seen.append(invocation.args)
            return "", "", 0

        spy = mox.spy("tool").runs(handler)
        mox.__enter__()
        mox.replay()
        path = require_shim_dir(mox.environment) / "tool"
        run([str(path), "run"], shell=False)
        run([str(path), "run", "--fail"], shell=False)
        mox.verify()
        assert seen == [["run"], ["run", "--fail"]]
        assert spy.call_count == 2

    # ------------------------------------------------------------------
    def _run_counted_spy_in_with_block(
        self,
        run: cabc.Callable[..., subprocess.CompletedProcess[str]],
    ) -> None:
        """Run one explicitly counted spy inside a ``with`` block."""
        with CmdMox() as mox:
            mox.spy("hi").returns(stdout="hello").times_called(2)
            mox.replay()
            path = require_shim_dir(mox.environment) / "hi"
            run([str(path)], shell=False)

    # ------------------------------------------------------------------
    def test_context_manager_enforces_explicit_spy_count(
        self, run: cabc.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """Leaving a ``with`` block verifies an explicit spy count."""
        with pytest.raises(UnfulfilledExpectationError):
            self._run_counted_spy_in_with_block(run)

    # ------------------------------------------------------------------
    def test_ordered_spy_mismatch_fails_verification(
        self, run: cabc.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """An ordered spy sequence that is violated fails verification."""
        mox = CmdMox()
        mox.spy("first").returns(stdout="1").in_order().times_called(2)
        mox.spy("second").returns(stdout="2").in_order().times_called(1)
        mox.__enter__()
        mox.replay()
        path_first = require_shim_dir(mox.environment) / "first"
        path_second = require_shim_dir(mox.environment) / "second"
        run([str(path_second)], shell=False)
        run([str(path_first)], shell=False)
        with pytest.raises(VerificationError):
            mox.verify()

    # ------------------------------------------------------------------
    def test_replay_backed_spy_explicit_count_is_verified(self, tmp_path: Path) -> None:
        """A replay-backed spy honours an explicit count."""
        fixture_path = write_minimal_replay_fixture(tmp_path)
        mox = CmdMox()
        mox.spy("git").replay(fixture_path).times_called(1)
        mox.__enter__()
        mox.replay()
        mox._handle_invocation(Invocation("git", ["status"], "", {}))
        mox.verify()

    # ------------------------------------------------------------------
    ASSERTION_FAILURE_SCENARIOS: typ.ClassVar[list[dict[str, typ.Any]]] = [
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
            "name": "validate_optional_attr_raises_on_mismatch",
            "setup": lambda self, run: self._create_spy_with_invocation(
                "hi", [], "", {}
            ),
            "method": "_validate_optional_attr",
            "args": lambda spy: ("label", "actual", "expected"),
            "kwargs": lambda spy: {},
            "expected_message": (
                "'hi' called with label 'actual', expected 'expected'"
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
        ids=operator.itemgetter("name"),
    )
    def test_spy_assertion_failures(
        self,
        run: cabc.Callable[..., subprocess.CompletedProcess[str]],
        scenario: dict[str, typ.Any],
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
            "_validate_optional_attr": lambda s: s._validate_optional_attr(
                "label", "expected", "expected"
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


class TestSpyCountVerification:
    """Edge cases where only one verifier can catch the defect."""

    # ------------------------------------------------------------------
    def test_uncounted_ordered_spy_single_call_passes(
        self, run: cabc.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """An ordered spy without an explicit count accepts its default one call."""
        mox = CmdMox()
        mox.spy("tool").returns(stdout="ok").in_order()
        mox.__enter__()
        mox.replay()
        path = require_shim_dir(mox.environment) / "tool"
        run([str(path)], shell=False)
        mox.verify()

    # ------------------------------------------------------------------
    def test_uncounted_ordered_spy_missing_call_fails(self) -> None:
        """An ordered spy without an explicit count still requires one call."""
        mox = CmdMox()
        mox.spy("tool").returns(stdout="ok").in_order()
        mox.__enter__()
        mox.replay()
        with pytest.raises(UnfulfilledExpectationError):
            mox.verify()

    # ------------------------------------------------------------------
    def test_uncounted_ordered_spy_extra_call_fails(
        self, run: cabc.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """An ordered spy without an explicit count rejects a second call."""
        mox = CmdMox()
        mox.spy("tool").returns(stdout="ok").in_order()
        mox.__enter__()
        mox.replay()
        path = require_shim_dir(mox.environment) / "tool"
        run([str(path)], shell=False)
        run([str(path)], shell=False)
        with pytest.raises(UnexpectedCommandError):
            mox.verify()

    # ------------------------------------------------------------------
    def test_ordered_spy_wrong_order_with_met_counts_fails(
        self, run: cabc.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """Order is enforced even when every explicit count is met."""
        mox = CmdMox()
        mox.spy("first").returns(stdout="1").in_order().times_called(2)
        mox.spy("second").returns(stdout="2").in_order().times_called(2)
        mox.__enter__()
        mox.replay()
        path_first = require_shim_dir(mox.environment) / "first"
        path_second = require_shim_dir(mox.environment) / "second"
        # Every count is satisfied, but the interleaving breaks the declared
        # order, so only OrderVerifier can catch this sequence.
        run([str(path_second)], shell=False)
        run([str(path_first)], shell=False)
        run([str(path_second)], shell=False)
        run([str(path_first)], shell=False)
        with pytest.raises(UnexpectedCommandError):
            mox.verify()

    # ------------------------------------------------------------------
    def test_replay_backed_spy_explicit_count_under_count_fails(
        self, tmp_path: Path
    ) -> None:
        """Consuming the fixture once is not enough when two calls are expected."""
        fixture_path = write_minimal_replay_fixture(tmp_path)
        mox = CmdMox()
        mox.spy("git").replay(fixture_path).times_called(2)
        mox.__enter__()
        mox.replay()
        mox._handle_invocation(Invocation("git", ["status"], "", {}))
        with pytest.raises(UnfulfilledExpectationError):
            mox.verify()
