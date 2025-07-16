"""Unit tests for :mod:`cmd_mox.controller`."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from cmd_mox.controller import CmdMox


def test_cmdmox_stub_records_invocation() -> None:
    """Stubbed command returns configured output and journal records call."""
    original_path = os.environ["PATH"]
    mox = CmdMox()
    mox.stub("hello").returns(stdout="hi")
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / "hello"
    result = subprocess.run(  # noqa: S603
        [str(cmd_path)], capture_output=True, text=True, check=True
    )
    mox.verify()

    assert result.stdout.strip() == "hi"
    assert len(mox.journal) == 1
    assert mox.journal[0].command == "hello"
    assert os.environ["PATH"] == original_path
