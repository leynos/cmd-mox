"""Windows-specific unit tests for the environment manager."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cmd_mox import EnvironmentManager, create_shim_symlinks

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows-only environment tests"
)


def _collect_pathext(value: str) -> set[str]:
    return {item.strip().upper() for item in value.split(os.pathsep) if item.strip()}


def test_environment_manager_injects_cmd_into_pathext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EnvironmentManager should ensure PATHEXT recognises .CMD launchers."""
    monkeypatch.setenv("PATHEXT", ".COM;.EXE")

    with EnvironmentManager():
        pathext = os.environ["PATHEXT"]
        assert ".CMD" in _collect_pathext(pathext)

    assert os.environ["PATHEXT"] == ".COM;.EXE"


def test_create_windows_shim_emits_batch_launcher() -> None:
    """Windows shims are emitted as CRLF-delimited batch launchers."""
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        create_shim_symlinks(env.shim_dir, ["cmd-mock"])

        shim_path = Path(env.shim_dir) / "cmd-mock.cmd"
        assert shim_path.exists()
        contents = shim_path.read_bytes()
        assert contents.startswith(b"@echo off\r\n")
        assert contents.endswith(b"%*\r\n")
