"""Tests for invocation journal capturing."""

from __future__ import annotations

import os
import typing as t

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess
    from pathlib import Path

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation


def test_journal_records_full_invocation(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Journal records command, arguments, stdin, and environment."""
    with CmdMox(verify_on_exit=False) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = t.cast("Path", mox.environment.shim_dir) / "rec"
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
) -> None:
    """Captured env is isolated from later mutations."""
    with CmdMox(verify_on_exit=False) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = t.cast("Path", mox.environment.shim_dir) / "rec"
        env = os.environ | {"EXTRA": "1"}
        result = run([str(cmd_path)], env=env)
        env["EXTRA"] = "2"
        os.environ["EXTRA"] = "3"
        mox.verify()

    assert result.stdout == "ok"
    inv = mox.journal[0]
    assert inv.env["EXTRA"] == "1"
    assert inv.stdout == "ok"
    assert inv.stderr == ""
    assert inv.exit_code == 0


def test_journal_prunes_to_maxlen(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Old journal entries are discarded once the max length is exceeded."""
    with CmdMox(verify_on_exit=False, max_journal_entries=2) as mox:
        mox.stub("rec").returns(stdout="ok")
        mox.replay()
        cmd_path = t.cast("Path", mox.environment.shim_dir) / "rec"
        for i in range(3):
            run([str(cmd_path), str(i)])
        mox.verify()

    assert len(mox.journal) == 2
    assert [inv.args for inv in mox.journal] == [["1"], ["2"]]


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
