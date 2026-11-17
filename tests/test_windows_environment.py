"""Windows-specific unit tests for the environment manager."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cmd_mox import CmdMox, EnvironmentManager, create_shims, shimgen
from cmd_mox.environment import _ensure_windows_pathext

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows-only environment tests"
)


def _collect_pathext(value: str) -> set[str]:
    """Return the upper-cased, non-empty PATHEXT entries in *value* as a set."""
    return {item.strip().upper() for item in value.split(os.pathsep) if item.strip()}


def test_environment_manager_injects_cmd_into_pathext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EnvironmentManager should ensure PATHEXT recognises .CMD launchers."""
    monkeypatch.setenv("PATHEXT", ".COM;.EXE")

    with EnvironmentManager():
        pathext = os.environ["PATHEXT"]
        assert ".CMD" in _collect_pathext(pathext), "Assertion failed"

    assert os.environ["PATHEXT"] == ".COM;.EXE", "Assertion failed"


def test_environment_manager_handles_empty_pathext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EnvironmentManager should handle empty PATHEXT and inject .CMD."""
    monkeypatch.setenv("PATHEXT", "")

    with EnvironmentManager():
        pathext = os.environ["PATHEXT"]
        assert ".CMD" in _collect_pathext(pathext), "Assertion failed"

    assert not os.environ["PATHEXT"], "Assertion failed"


def test_environment_manager_handles_missing_pathext(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """EnvironmentManager should handle missing PATHEXT and inject .CMD."""
    monkeypatch.delenv("PATHEXT", raising=False)

    with EnvironmentManager():
        pathext = os.environ.get("PATHEXT", "")
        assert ".CMD" in _collect_pathext(pathext), "Assertion failed"

    assert "PATHEXT" not in os.environ, "Assertion failed"


def test_ensure_windows_pathext_appends_missing_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_windows_pathext should append .CMD to existing PATHEXT entries."""
    monkeypatch.setenv("PATHEXT", ".COM;.EXE")
    original = os.environ.copy()

    _ensure_windows_pathext(original)

    pathext = os.environ["PATHEXT"]
    assert _collect_pathext(pathext) == {".COM", ".EXE", ".CMD"}, "Assertion failed"
    assert original["PATHEXT"] == ".COM;.EXE", "Assertion failed"


def test_ensure_windows_pathext_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling _ensure_windows_pathext twice should not duplicate entries."""
    monkeypatch.setenv("PATHEXT", ".COM;.EXE;.CMD")
    original = os.environ.copy()

    _ensure_windows_pathext(original)

    pathext = os.environ["PATHEXT"]
    assert pathext == ".COM;.EXE;.CMD", "Assertion failed"


def test_ensure_windows_pathext_populates_default_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_ensure_windows_pathext should supply a sane default when PATHEXT is absent."""
    monkeypatch.delenv("PATHEXT", raising=False)
    original = os.environ.copy()

    _ensure_windows_pathext(original)

    pathext = os.environ.get("PATHEXT", "")
    assert ".CMD" in _collect_pathext(pathext), "Assertion failed"
    assert "PATHEXT" not in original, "Assertion failed"


def test_create_windows_shim_emits_batch_launcher() -> None:
    """Windows shims are emitted as CRLF-delimited batch launchers."""
    with EnvironmentManager() as env:
        assert env.shim_dir is not None, "Assertion failed"
        create_shims(env.shim_dir, ["cmd-mock"])

        shim_path = Path(env.shim_dir) / "cmd-mock.cmd"
        assert shim_path.exists(), "Assertion failed"
        contents = shim_path.read_bytes()
        assert contents.startswith(b"@echo off\r\n"), "Assertion failed"
        assert b'set "CMOX_SHIM_COMMAND=%~n0"\r\n' in contents, "Assertion failed"
        assert contents.endswith(b"%*\r\n"), "Assertion failed"


def test_late_registration_writes_launcher_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A matching launcher is written once across repeated registrations."""
    created: list[str] = []
    original_create = shimgen._create_windows_shim

    def counting_create(directory: Path, name: str) -> Path:
        """Record each launcher write, then delegate to the real creator."""
        created.append(name)
        return original_create(directory, name)

    monkeypatch.setattr(shimgen, "_create_windows_shim", counting_create)

    with CmdMox(verify_on_exit=False) as mox:
        mox.replay()
        mox.register_command("tool")
        assert mox.environment.shim_dir is not None, "Assertion failed"
        launcher = Path(mox.environment.shim_dir) / "tool.cmd"
        first_contents = launcher.read_bytes()

        mox.register_command("tool")

        assert created.count("tool") == 1, "Assertion failed"
        assert launcher.read_bytes() == first_contents, "Assertion failed"


def test_late_registration_reports_foreign_launcher_collision() -> None:
    """A foreign .cmd entry raises and is preserved byte for byte."""
    with CmdMox(verify_on_exit=False) as mox:
        mox.replay()
        assert mox.environment.shim_dir is not None, "Assertion failed"
        foreign = Path(mox.environment.shim_dir) / "other.cmd"
        foreign.write_bytes(b"foreign launcher")

        with pytest.raises(FileExistsError, match="not a cmd-mox launcher"):
            mox.register_command("other")

        assert foreign.read_bytes() == b"foreign launcher", "Assertion failed"


def test_environment_manager_accepts_case_tweaked_shim_dir() -> None:
    """Cleanup should work even if shim_dir casing changes mid-test."""
    with EnvironmentManager() as env:
        assert env.shim_dir is not None, "Assertion failed"
        original = Path(env.shim_dir)
        env.shim_dir = Path(str(env.shim_dir).upper())
    assert not original.exists(), "Assertion failed"
