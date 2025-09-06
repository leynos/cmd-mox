"""Tests for invocation journal capturing."""

from __future__ import annotations

import ast
import os
import typing as t
from pathlib import Path

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess

    import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation


def test_journal_records_full_invocation(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Journal records command, arguments, stdin, and environment."""
    with CmdMox(verify_on_exit=False) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = t.cast(Path, mox.environment.shim_dir) / "rec"  # noqa: TC006
        env = os.environ | {"EXTRA": "1"}
        result = run([str(cmd_path), "a", "b"], input="payload", env=env)
        mox.verify()

    assert result.stdout == "ok"
    assert len(mox.journal) == 1
    inv = mox.journal[0]
    assert inv.command == "rec"
    assert inv.args == ["a", "b"]
    assert inv.stdin == "payload"
    assert inv.env["EXTRA"] == "1"
    assert inv.stdout == "ok"
    assert inv.stderr == ""
    assert inv.exit_code == 0


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


def test_journal_prunes_to_maxlen(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Old journal entries are discarded once the max length is exceeded."""
    with CmdMox(verify_on_exit=False, max_journal_entries=2) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = t.cast(Path, mox.environment.shim_dir) / "rec"  # noqa: TC006
        for i in range(3):
            run([str(cmd_path), str(i)])
        mox.verify()

    assert len(mox.journal) == 2
    assert [inv.args for inv in mox.journal] == [["1"], ["2"]]


def test_journal_prunes_to_maxlen_one(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Only the most recent entry is kept when max_journal_entries=1."""
    with CmdMox(verify_on_exit=False, max_journal_entries=1) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = t.cast(Path, mox.environment.shim_dir) / "rec"  # noqa: TC006
        for i in range(3):
            run([str(cmd_path), str(i)])
        mox.verify()

    assert len(mox.journal) == 1
    assert [inv.args for inv in mox.journal] == [["2"]]


def test_journal_unlimited(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Journal retains all entries when size is unbounded."""
    with CmdMox(verify_on_exit=False, max_journal_entries=None) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = t.cast(Path, mox.environment.shim_dir) / "rec"  # noqa: TC006
        for i in range(3):
            run([str(cmd_path), str(i)])
        mox.verify()

    assert len(mox.journal) == 3
    assert [inv.args for inv in mox.journal] == [["0"], ["1"], ["2"]]


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
