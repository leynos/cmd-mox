"""Windows-specific unit tests for the environment manager."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cmd_mox import EnvironmentManager, create_shim_symlinks
from cmd_mox.environment import _ensure_windows_pathext

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


def test_environment_manager_handles_empty_pathext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EnvironmentManager should handle empty PATHEXT and inject .CMD."""
    monkeypatch.setenv("PATHEXT", "")

    with EnvironmentManager():
        pathext = os.environ["PATHEXT"]
        assert ".CMD" in _collect_pathext(pathext)

    assert os.environ["PATHEXT"] == ""


def test_environment_manager_handles_missing_pathext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EnvironmentManager should handle missing PATHEXT and inject .CMD."""
    monkeypatch.delenv("PATHEXT", raising=False)

    with EnvironmentManager():
        pathext = os.environ.get("PATHEXT", "")
        assert ".CMD" in _collect_pathext(pathext)

    assert "PATHEXT" not in os.environ


def test_ensure_windows_pathext_appends_missing_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_windows_pathext should append .CMD to existing PATHEXT entries."""
    monkeypatch.setenv("PATHEXT", ".COM;.EXE")
    original = os.environ.copy()

    _ensure_windows_pathext(original)

    pathext = os.environ["PATHEXT"]
    assert _collect_pathext(pathext) == {".COM", ".EXE", ".CMD"}
    assert original["PATHEXT"] == ".COM;.EXE"


def test_ensure_windows_pathext_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling _ensure_windows_pathext twice should not duplicate entries."""
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.CMD")
    original = os.environ.copy()

    _ensure_windows_pathext(original)

    pathext = os.environ["PATHEXT"]
    assert pathext == ".COM;.EXE;.CMD"


def test_ensure_windows_pathext_populates_default_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_windows_pathext should supply a sane default when PATHEXT is absent."""
    monkeypatch.delenv("PATHEXT", raising=False)
    original = os.environ.copy()

    _ensure_windows_pathext(original)

    pathext = os.environ.get("PATHEXT", "")
    assert ".CMD" in _collect_pathext(pathext)
    assert "PATHEXT" not in original


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
