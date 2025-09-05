"""Tests for invocation journal capturing."""

from __future__ import annotations

import os
import subprocess
import typing as t
from pathlib import Path

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess

from cmd_mox.controller import CmdMox


def test_journal_records_full_invocation(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Journal records command, arguments, stdin, and environment."""
    mox = CmdMox()
    mox.stub("rec").returns(stdout="ok")
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "rec"
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
