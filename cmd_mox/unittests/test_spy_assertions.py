"""Tests for spy assertion helpers."""

import os
import subprocess
import typing as t
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox, CommandDouble
from cmd_mox.ipc import Invocation


class TestSpyAssertions:
    """Tests covering the spy assertion helper API."""

    # ------------------------------------------------------------------
    def _create_spy_and_run_command(
        self,
        run: t.Callable[..., subprocess.CompletedProcess[str]],
        cmd_args: list[str] | None = None,
        stdin_input: str | None = None,
        env: dict[str, str] | None = None,
        cmd_name: str = "hi",
        stdout_return: str = "hello",
    ) -> tuple[CmdMox, CommandDouble]:
        """Return a ``(mox, spy)`` pair after running a command.

        This helper performs the full mox lifecycle and executes the command
        with the provided arguments, stdin, and environment.
        """
        mox = CmdMox()
        spy = mox.spy(cmd_name).returns(stdout=stdout_return)
        mox.__enter__()
        mox.replay()

        full_env = dict(os.environ, **(env or {}))
        cmd_path = Path(mox.environment.shim_dir) / cmd_name
        run([str(cmd_path), *(cmd_args or [])], env=full_env, input=stdin_input)

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
        method_name: str,
        expected_message: str | None = None,
        *args: object,
        **kwargs: object,
    ) -> None:
        """Invoke ``spy.method_name`` and assert it raises ``AssertionError``."""
        method = getattr(spy, method_name)
        with pytest.raises(AssertionError) as exc:
            method(*args, **kwargs)
        if expected_message is not None:
            assert str(exc.value) == expected_message

    # ------------------------------------------------------------------
    def test_spy_assert_called_and_called_with(
        self, run: t.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """Spy exposes assert helpers mirroring unittest.mock."""
        _, spy = self._create_spy_and_run_command(
            run, cmd_args=["foo", "bar"], stdin_input="stdin"
        )
        spy.assert_called()
        spy.assert_called_with("foo", "bar", stdin="stdin")

    # ------------------------------------------------------------------
    def test_spy_assert_called_with_env(
        self, run: t.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """assert_called_with validates the environment mapping."""
        _, spy = self._create_spy_and_run_command(
            run, cmd_args=["foo"], stdin_input="stdin", env={"MYVAR": "VALUE"}
        )
        actual_env = spy.invocations[0].env
        spy.assert_called_with("foo", stdin="stdin", env=actual_env)

        bad_env = dict(actual_env, MYVAR="DIFFERENT")
        self._assert_raises_assertion_error(
            spy,
            "assert_called_with",
            f"'hi' called with env {actual_env!r}, expected {bad_env!r}",
            "foo",
            stdin="stdin",
            env=bad_env,
        )

    # ------------------------------------------------------------------
    def test_spy_assert_called_raises_when_never_called(self) -> None:
        """assert_called raises when the spy was never invoked."""
        mox = CmdMox()
        spy = mox.spy("hi")
        mox.__enter__()
        mox.replay()
        mox.verify()

        self._assert_raises_assertion_error(spy, "assert_called")
        spy.assert_not_called()

    # ------------------------------------------------------------------
    def test_spy_assert_not_called_raises_when_called(
        self, run: t.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """assert_not_called raises if the spy was invoked."""
        _, spy = self._create_spy_and_run_command(run)
        self._assert_raises_assertion_error(
            spy,
            "assert_not_called",
            "Expected 'hi' to be uncalled but it was called 1 time(s); last args=[]",
        )

    # ------------------------------------------------------------------
    def test_spy_assert_called_with_mismatched_args(
        self, run: t.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """assert_called_with raises when arguments differ."""
        _, spy = self._create_spy_and_run_command(run, cmd_args=["actual"])
        self._assert_raises_assertion_error(
            spy,
            "assert_called_with",
            "'hi' called with args ['actual'], expected ['expected']",
            "expected",
        )

    # ------------------------------------------------------------------
    def test_spy_assert_called_with_partial_args(
        self, run: t.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """assert_called_with fails for subset or superset of args."""
        _, spy = self._create_spy_and_run_command(run, cmd_args=["foo", "bar"])
        self._assert_raises_assertion_error(
            spy,
            "assert_called_with",
            "'hi' called with args ['foo', 'bar'], expected ['foo']",
            "foo",
        )
        self._assert_raises_assertion_error(
            spy,
            "assert_called_with",
            "'hi' called with args ['foo', 'bar'], expected ['foo', 'bar', 'baz']",
            "foo",
            "bar",
            "baz",
        )

    # ------------------------------------------------------------------
    def test_spy_assert_called_with_mismatched_stdin(
        self, run: t.Callable[..., subprocess.CompletedProcess[str]]
    ) -> None:
        """assert_called_with raises when stdin differs."""
        _, spy = self._create_spy_and_run_command(run, stdin_input="actual")
        self._assert_raises_assertion_error(
            spy,
            "assert_called_with",
            "'hi' called with stdin 'actual', expected 'expected'",
            stdin="expected",
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
            "_validate_spy_usage",
            "assert_called_with() is only valid for spies",
            "assert_called_with",
        )

    # ------------------------------------------------------------------
    def test_get_last_invocation_behaviour(self) -> None:
        """_get_last_invocation returns the last call and errors when absent."""
        mox = CmdMox()
        spy = mox.spy("hi")
        self._assert_raises_assertion_error(
            spy,
            "_get_last_invocation",
            "Expected 'hi' to be called but it was never called",
        )
        invocation = Invocation("hi", ["foo"], "", {})
        spy.invocations.append(invocation)
        assert spy._get_last_invocation() is invocation

    # ------------------------------------------------------------------
    def test_validate_arguments_raises_on_mismatch(self) -> None:
        """_validate_arguments compares expected and actual args."""
        spy = self._create_spy_with_invocation("hi", ["foo"], "", {})
        invocation = spy.invocations[0]
        self._assert_raises_assertion_error(
            spy,
            "_validate_arguments",
            "'hi' called with args ['foo'], expected ['bar']",
            invocation,
            ("bar",),
        )
        spy._validate_arguments(invocation, ("foo",))

    # ------------------------------------------------------------------
    def test_validate_stdin_raises_on_mismatch(self) -> None:
        """_validate_stdin compares provided stdin against the invocation."""
        spy = self._create_spy_with_invocation("hi", [], "actual", {})
        invocation = spy.invocations[0]
        self._assert_raises_assertion_error(
            spy,
            "_validate_stdin",
            "'hi' called with stdin 'actual', expected 'expected'",
            invocation,
            "expected",
        )
        spy._validate_stdin(invocation, "actual")

    # ------------------------------------------------------------------
    def test_validate_environment_raises_on_mismatch(self) -> None:
        """_validate_environment compares environment mappings."""
        spy = self._create_spy_with_invocation("hi", [], "", {"A": "1"})
        invocation = spy.invocations[0]
        self._assert_raises_assertion_error(
            spy,
            "_validate_environment",
            "'hi' called with env {'A': '1'}, expected {'B': '2'}",
            invocation,
            {"B": "2"},
        )
        spy._validate_environment(invocation, {"A": "1"})
