"""Tests for spy assertion helpers."""

import os
import subprocess
import typing as t
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation


def test_spy_assert_called_and_called_with(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Spy exposes assert helpers mirroring unittest.mock."""
    mox = CmdMox()
    spy = mox.spy("hi").returns(stdout="hello")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hi"
    run([str(cmd_path), "foo", "bar"], input="stdin")

    mox.verify()

    spy.assert_called()
    spy.assert_called_with("foo", "bar", stdin="stdin")


def test_spy_assert_called_with_env(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """assert_called_with validates the environment mapping."""
    mox = CmdMox()
    spy = mox.spy("hi").returns(stdout="hello")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hi"
    env = dict(os.environ, MYVAR="VALUE")
    run([str(cmd_path), "foo"], env=env, input="stdin")

    mox.verify()

    spy.assert_called_with("foo", stdin="stdin", env=env)
    with pytest.raises(AssertionError):
        spy.assert_called_with(
            "foo", stdin="stdin", env=dict(os.environ, MYVAR="OTHER")
        )


def test_spy_assert_called_raises_when_never_called() -> None:
    """assert_called raises when the spy was never invoked."""
    mox = CmdMox()
    spy = mox.spy("hi")
    mox.__enter__()
    mox.replay()
    mox.verify()

    with pytest.raises(AssertionError):
        spy.assert_called()
    spy.assert_not_called()


def test_spy_assert_not_called_raises_when_called(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """assert_not_called raises if the spy was invoked."""
    mox = CmdMox()
    spy = mox.spy("hi").returns(stdout="hello")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hi"
    run([str(cmd_path)])

    mox.verify()

    with pytest.raises(AssertionError):
        spy.assert_not_called()


def test_spy_assert_called_with_mismatched_args(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """assert_called_with raises when arguments differ."""
    mox = CmdMox()
    spy = mox.spy("hi").returns(stdout="hello")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hi"
    run([str(cmd_path), "actual"])

    mox.verify()

    with pytest.raises(AssertionError):
        spy.assert_called_with("expected")


def test_spy_assert_called_with_partial_args(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """assert_called_with fails for subset or superset of args."""
    mox = CmdMox()
    spy = mox.spy("hi").returns(stdout="hello")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hi"
    run([str(cmd_path), "foo", "bar"])

    mox.verify()

    with pytest.raises(AssertionError):
        spy.assert_called_with("foo")
    with pytest.raises(AssertionError):
        spy.assert_called_with("foo", "bar", "baz")


def test_spy_assert_called_with_mismatched_stdin(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """assert_called_with raises when stdin differs."""
    mox = CmdMox()
    spy = mox.spy("hi").returns(stdout="hello")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hi"
    run([str(cmd_path)], input="actual")

    mox.verify()

    with pytest.raises(AssertionError):
        spy.assert_called_with(stdin="expected")


def test_validate_spy_usage_only_allows_spies() -> None:
    """_validate_spy_usage permits spies and rejects other doubles."""
    mox = CmdMox()
    spy = mox.spy("spy_cmd")
    spy._validate_spy_usage("assert_called_with")
    mock = mox.mock("mock_cmd")
    with pytest.raises(AssertionError) as exc:
        mock._validate_spy_usage("assert_called_with")
    assert str(exc.value) == "assert_called_with() is only valid for spies"


def test_get_last_invocation_behaviour() -> None:
    """_get_last_invocation returns the last call and errors when absent."""
    mox = CmdMox()
    spy = mox.spy("hi")
    with pytest.raises(AssertionError) as exc:
        spy._get_last_invocation()
    assert str(exc.value) == "Expected 'hi' to be called but it was never called"
    invocation = Invocation("hi", ["foo"], "", {})
    spy.invocations.append(invocation)
    assert spy._get_last_invocation() is invocation


def test_validate_arguments_raises_on_mismatch() -> None:
    """_validate_arguments compares expected and actual args."""
    mox = CmdMox()
    spy = mox.spy("hi")
    invocation = Invocation("hi", ["foo"], "", {})
    spy.invocations.append(invocation)
    with pytest.raises(AssertionError) as exc:
        spy._validate_arguments(invocation, ("bar",))
    assert str(exc.value) == "'hi' called with args ['foo'], expected ['bar']"
    spy._validate_arguments(invocation, ("foo",))


def test_validate_stdin_raises_on_mismatch() -> None:
    """_validate_stdin compares provided stdin against the invocation."""
    mox = CmdMox()
    spy = mox.spy("hi")
    invocation = Invocation("hi", [], "actual", {})
    spy.invocations.append(invocation)
    with pytest.raises(AssertionError) as exc:
        spy._validate_stdin(invocation, "expected")
    assert str(exc.value) == "'hi' called with stdin 'actual', expected 'expected'"
    spy._validate_stdin(invocation, "actual")


def test_validate_environment_raises_on_mismatch() -> None:
    """_validate_environment compares environment mappings."""
    mox = CmdMox()
    spy = mox.spy("hi")
    invocation = Invocation("hi", [], "", {"A": "1"})
    spy.invocations.append(invocation)
    with pytest.raises(AssertionError) as exc:
        spy._validate_environment(invocation, {"B": "2"})
    assert str(exc.value) == "'hi' called with env {'A': '1'}, expected {'B': '2'}"
    spy._validate_environment(invocation, {"A": "1"})
