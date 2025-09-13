"""Tests for invocation journal capturing."""

from __future__ import annotations

import ast
import dataclasses as dc
import os
import subprocess
import typing as t

if t.TYPE_CHECKING:
    from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.expectations import SENSITIVE_ENV_KEY_TOKENS
from cmd_mox.ipc import Invocation
from tests.helpers.controller import (
    CommandExecution,
    JournalEntryExpectation,
    execute_command_with_details,
    verify_journal_entry_details,
)


class StubReturns(t.TypedDict, total=False):
    """Optional stub return values."""

    stdout: str
    stderr: str
    exit_code: int


def _shim_cmd_path(mox: CmdMox, name: str) -> Path:
    """Return shim path for a command; requires prior mox.replay()."""
    sd = mox.environment.shim_dir
    assert sd is not None, "shim_dir is None; did you forget to call mox.replay()?"
    return sd / name


def _setup_and_execute_command(
    mox: CmdMox,
    stub_name: str,
    stub_returns: StubReturns,
    *,
    args: str,
    stdin: str,
    env_var: str,
    env_val: str,
    check: bool,
) -> subprocess.CompletedProcess[str]:
    """Create a stub, run it once, and return the result."""
    mox.stub(stub_name).returns(**stub_returns)
    mox.replay()
    cmd_path = _shim_cmd_path(mox, stub_name)
    params = CommandExecution(
        cmd=str(cmd_path),
        args=args,
        stdin=stdin,
        env_var=env_var,
        env_val=env_val,
        check=check,
    )
    return execute_command_with_details(mox, params)


def _assert_single_journal_entry(
    mox: CmdMox, expectation: JournalEntryExpectation
) -> None:
    """Verify journal length and entry details."""
    assert len(mox.journal) == 1
    verify_journal_entry_details(mox, expectation)


@dc.dataclass(slots=True, frozen=True)
class InvocationTestCase:
    """Parameters for invocation journal tests."""

    stub_name: str
    stub_returns: StubReturns
    args: str
    stdin: str
    env_var: str
    env_val: str
    expected_stdout: str
    expected_stderr: str
    expected_exit: int


@pytest.mark.parametrize(
    "test_case",
    [
        InvocationTestCase(
            stub_name="rec",
            stub_returns={"stdout": "ok"},
            args="a b",
            stdin="payload",
            env_var="EXTRA",
            env_val="1",
            expected_stdout="ok",
            expected_stderr="",
            expected_exit=0,
        ),
        InvocationTestCase(
            stub_name="failcmd",
            stub_returns={"stdout": "", "stderr": "error occurred", "exit_code": 2},
            args="--fail",
            stdin="input",
            env_var="FAILMODE",
            env_val="true",
            expected_stdout="",
            expected_stderr="error occurred",
            expected_exit=2,
        ),
    ],
    ids=("success", "failure"),
)
def test_journal_records_invocation(test_case: InvocationTestCase) -> None:
    """Journal records both successful and failed command invocations."""
    with CmdMox(verify_on_exit=False) as mox:
        result = _setup_and_execute_command(
            mox,
            test_case.stub_name,
            test_case.stub_returns,
            args=test_case.args,
            stdin=test_case.stdin,
            env_var=test_case.env_var,
            env_val=test_case.env_val,
            check=test_case.expected_exit == 0,
        )
        mox.verify()

    assert result.stdout == test_case.expected_stdout
    assert result.stderr == test_case.expected_stderr
    assert result.returncode == test_case.expected_exit
    expectation = JournalEntryExpectation(
        cmd=test_case.stub_name,
        args=test_case.args,
        stdin=test_case.stdin,
        env_var=test_case.env_var,
        env_val=test_case.env_val,
        stdout=test_case.expected_stdout,
        stderr=test_case.expected_stderr,
        exit_code=test_case.expected_exit,
    )
    _assert_single_journal_entry(mox, expectation)


def test_journal_records_failed_invocation_raises_still_journaled() -> None:
    """Journal records failed command even when subprocess raises."""
    with CmdMox(verify_on_exit=False) as mox:
        with pytest.raises(subprocess.CalledProcessError):
            _setup_and_execute_command(
                mox,
                "failcmd",
                {"stdout": "", "stderr": "error occurred", "exit_code": 2},
                args="--fail",
                stdin="input",
                env_var="FAILMODE",
                env_val="true",
                check=True,
            )
        mox.verify()
    expectation = JournalEntryExpectation(
        cmd="failcmd",
        args="--fail",
        stdin="input",
        env_var="FAILMODE",
        env_val="true",
        stdout="",
        stderr="error occurred",
        exit_code=2,
    )
    _assert_single_journal_entry(mox, expectation)


def test_journal_env_is_deep_copied(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Captured env is isolated from later mutations."""
    with CmdMox(verify_on_exit=False) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = _shim_cmd_path(mox, "rec")
        run([str(cmd_path)], env=os.environ | {"EXTRA": "1"})
        monkeypatch.setenv("EXTRA", "3")
        run([str(cmd_path)], env=os.environ | {"EXTRA": "2"})
        mox.verify()

    assert [inv.env.get("EXTRA") for inv in mox.journal] == ["1", "2"]


@pytest.mark.parametrize("invalid_maxlen", [0, -1, -10])
def test_journal_pruning_invalid_maxlen(invalid_maxlen: int) -> None:
    """CmdMox raises ValueError for zero or negative max_journal_entries."""
    with pytest.raises(ValueError, match="max_journal_entries"):
        CmdMox(max_journal_entries=invalid_maxlen)


@pytest.mark.parametrize(
    ("maxlen", "expected"),
    [
        (2, [["1"], ["2"]]),
        (1, [["2"]]),
        (None, [["0"], ["1"], ["2"]]),
    ],
)
def test_journal_pruning(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
    maxlen: int | None,
    expected: list[list[str]],
) -> None:
    """Journal retains recent entries based on max length."""
    with CmdMox(verify_on_exit=False, max_journal_entries=maxlen) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = _shim_cmd_path(mox, "rec")
        for i in range(3):
            run([str(cmd_path), str(i)])
        mox.verify()

    assert len(mox.journal) == len(expected)
    assert [inv.args for inv in mox.journal] == expected


def test_invocation_to_dict() -> None:
    """Invocation.to_dict returns a serializable mapping."""
    inv = Invocation(
        command="cmd",
        args=["a"],
        stdin="in",
        env={"X": "1"},
        stdout="out",
        stderr="err",
        exit_code=2,
    )
    assert inv.to_dict() == {
        "command": "cmd",
        "args": ["a"],
        "stdin": "in",
        "env": {"X": "1"},
        "stdout": "out",
        "stderr": "err",
        "exit_code": 2,
    }


@pytest.mark.parametrize(
    "key",
    [f"MY_{token.upper()}" for token in SENSITIVE_ENV_KEY_TOKENS] + ["pAsSwOrD"],
)
def test_invocation_repr_redacts_keys(key: str) -> None:
    """__repr__ redacts keys containing sensitive tokens."""
    secret = "super-secret"  # noqa: S105 - test value
    inv = Invocation(
        command="cmd",
        args=[],
        stdin="",
        env={key: secret},
        stdout="",
        stderr="",
        exit_code=0,
    )
    text = repr(inv)
    data = ast.literal_eval(text[len("Invocation(") : -1])
    assert data["env"][key] == "<redacted>"
    assert "super-secret" not in text


def test_invocation_repr_does_not_redact_benign_key() -> None:
    """__repr__ leaves non-sensitive env vars untouched."""
    inv = Invocation(
        command="cmd",
        args=[],
        stdin="",
        env={"MONKEY": "ok"},
        stdout="",
        stderr="",
        exit_code=0,
    )
    text = repr(inv)
    data = ast.literal_eval(text[len("Invocation(") : -1])
    assert data["env"]["MONKEY"] == "ok"


def test_invocation_repr_truncates_streams() -> None:
    """__repr__ truncates long stream fields."""
    long = "x" * 300
    inv = Invocation(
        command="cmd",
        args=[],
        stdin=long,
        env={},
        stdout=long,
        stderr=long,
        exit_code=0,
    )
    text = repr(inv)
    data = ast.literal_eval(text[len("Invocation(") : -1])
    for field in ("stdin", "stdout", "stderr"):
        val = data[field]
        assert len(val) <= 256
        assert val.endswith("…")
