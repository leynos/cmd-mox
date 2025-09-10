"""Tests for invocation journal capturing."""

from __future__ import annotations

import ast
import os
import typing as t
<<<<<<< HEAD
||||||| parent of fec38e5 (Refactor invocation test parameters)
from pathlib import Path
=======
from dataclasses import dataclass  # noqa: ICN003
from pathlib import Path
>>>>>>> fec38e5 (Refactor invocation test parameters)

import pytest

from tests.helpers.controller import (
    CommandExecution,
    JournalEntryExpectation,
    execute_command_with_details,
    verify_journal_entry_details,
)

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation


@dataclass
class InvocationTestCase:
    """Parameters for invocation journal tests."""

    stub_name: str
    stub_returns: dict[str, t.Any]
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
)
def test_journal_records_invocation(test_case: InvocationTestCase) -> None:
    """Journal records both successful and failed command invocations."""
    with CmdMox(verify_on_exit=False) as mox:
        mox.stub(test_case.stub_name).returns(**test_case.stub_returns)
        mox.replay()
<<<<<<< HEAD
        assert mox.environment.shim_dir is not None
        cmd_path = mox.environment.shim_dir / stub_name
||||||| parent of fec38e5 (Refactor invocation test parameters)
        cmd_path = t.cast(Path, mox.environment.shim_dir) / stub_name  # noqa: TC006
=======
        cmd_path = t.cast(Path, mox.environment.shim_dir) / test_case.stub_name  # noqa: TC006
>>>>>>> fec38e5 (Refactor invocation test parameters)
        params = CommandExecution(
            cmd=str(cmd_path),
            args=test_case.args,
            stdin=test_case.stdin,
            env_var=test_case.env_var,
            env_val=test_case.env_val,
            check=test_case.expected_exit == 0,
        )
        result = execute_command_with_details(mox, params)
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
    verify_journal_entry_details(mox, expectation)


def test_journal_env_is_deep_copied(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Captured env is isolated from later mutations."""
    with CmdMox(verify_on_exit=False) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        assert mox.environment.shim_dir is not None
        cmd_path = mox.environment.shim_dir / "rec"
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
        assert mox.environment.shim_dir is not None
        cmd_path = mox.environment.shim_dir / "rec"
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


def test_invocation_repr_redacts_sensitive_info() -> None:
    """__repr__ redacts secrets and truncates long streams."""
    secret = "super-secret"  # noqa: S105 - test value
    long = "x" * 300
    inv = Invocation(
        command="cmd",
        args=[],
        stdin=long,
        env={"API_KEY": secret, "PASSWORD": secret, "TOKEN": secret},
        stdout=long,
        stderr=long,
        exit_code=0,
    )
    text = repr(inv)
    data = ast.literal_eval(text[len("Invocation(") : -1])
    for key in ("API_KEY", "PASSWORD", "TOKEN"):
        assert data["env"][key] == "<redacted>"
    for field in ("stdin", "stdout", "stderr"):
        val = data[field]
        assert len(val) <= 256
        assert val.endswith("…")
