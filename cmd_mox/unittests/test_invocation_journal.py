"""Tests for invocation journal capturing."""

from __future__ import annotations

import ast
import os
import typing as t
from pathlib import Path

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


def _run_full_invocation() -> tuple[
    CmdMox, subprocess.CompletedProcess[str], JournalEntryExpectation
]:
    """Run the rec shim and return execution details."""
    with CmdMox(verify_on_exit=False) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = t.cast(Path, mox.environment.shim_dir) / "rec"  # noqa: TC006
        params = CommandExecution(
            cmd=str(cmd_path),
            args="a b",
            stdin="payload",
            env_var="EXTRA",
            env_val="1",
        )
        result = execute_command_with_details(mox, params)
        mox.verify()
    expectation = JournalEntryExpectation(
        cmd="rec",
        args="a b",
        stdin="payload",
        env_var="EXTRA",
        env_val="1",
        stdout="ok",
        stderr="",
        exit_code=0,
    )
    return mox, result, expectation


def test_journal_records_full_invocation() -> None:
    """Journal records command, arguments, stdin, and environment."""
    mox, result, expectation = _run_full_invocation()
    assert result.stdout == "ok"
    verify_journal_entry_details(mox, expectation)


def test_journal_env_is_deep_copied(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Captured env is isolated from later mutations."""
    with CmdMox(verify_on_exit=False) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = t.cast(Path, mox.environment.shim_dir) / "rec"  # noqa: TC006
        run([str(cmd_path)], env=os.environ | {"EXTRA": "1"})
        monkeypatch.setenv("EXTRA", "3")
        run([str(cmd_path)], env=os.environ | {"EXTRA": "2"})
        mox.verify()

    assert [inv.env.get("EXTRA") for inv in mox.journal] == ["1", "2"]


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
        cmd_path = t.cast(Path, mox.environment.shim_dir) / "rec"  # noqa: TC006
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
        env={"API_KEY": secret},
        stdout=long,
        stderr=long,
        exit_code=0,
    )
    text = repr(inv)
    data = ast.literal_eval(text[len("Invocation(") : -1])
    assert data["env"]["API_KEY"] == "<redacted>"
    for field in ("stdin", "stdout", "stderr"):
        val = data[field]
        assert len(val) <= 256
        assert val.endswith("…")
