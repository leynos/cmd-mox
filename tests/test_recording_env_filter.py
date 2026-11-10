"""Real-shim regression tests for recording environment filtering."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import typing as typ

import pytest

from cmd_mox.controller import CmdMox

if typ.TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.requires_unix_sockets

_COMMAND = "greet"
_SETTING_KEY = "GREET_SETTING"
_SETTING_VALUE = "portable"
_HOSTISH_KEY = "HOSTISH_VALUE"
_SCRIPT = "import os; print(os.environ.get('GREET_SETTING', ''))"


def _resolve_shim(name: str) -> str:
    """Return the shim path, using the platform's executable suffix rules.

    Returns
    -------
    str
        The absolute path of the shim placed on ``PATH`` by CmdMox.
    """
    resolved = shutil.which(name)
    assert resolved is not None, f"shim for {name!r} was not on PATH"
    return resolved


def _run_greet() -> subprocess.CompletedProcess[str]:
    """Invoke the greet shim with the shared script and no standard input.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed shim process.
    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - the shim path is resolved by the CmdMox environment under test
        [_resolve_shim(_COMMAND), "-c", _SCRIPT],
        input="",
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )


def test_unknown_command_recording_keeps_only_with_env_keys(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recorded unknown command keeps only its explicit ``.with_env()`` key."""
    fixture_path = tmp_path / "greet.json"
    monkeypatch.setenv(f"CMOX_REAL_COMMAND_{_COMMAND}", sys.executable)
    monkeypatch.setenv(_HOSTISH_KEY, "host-specific")

    with CmdMox() as mox:
        mox.spy(_COMMAND).with_env({_SETTING_KEY: _SETTING_VALUE}).passthrough().record(
            fixture_path
        )
        mox.replay()

        result = _run_greet()
        mox.verify()

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == _SETTING_VALUE

    fixture = json.loads(fixture_path.read_text())
    assert fixture["recordings"][0]["env_subset"] == {_SETTING_KEY: _SETTING_VALUE}


def test_recorded_unknown_command_replays_strictly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fixture recorded without host data replays under strict matching."""
    fixture_path = tmp_path / "greet.json"
    monkeypatch.setenv(f"CMOX_REAL_COMMAND_{_COMMAND}", sys.executable)
    monkeypatch.setenv(_HOSTISH_KEY, "host-specific")

    with CmdMox() as mox:
        mox.spy(_COMMAND).with_env({_SETTING_KEY: _SETTING_VALUE}).passthrough().record(
            fixture_path
        )
        mox.replay()
        _run_greet()
        mox.verify()

    # Removing the host-like variable mimics replay on another machine or CI.
    monkeypatch.delenv(_HOSTISH_KEY)

    with CmdMox() as mox:
        mox.spy(_COMMAND).with_env({_SETTING_KEY: _SETTING_VALUE}).replay(fixture_path)
        mox.replay()

        result = _run_greet()
        mox.verify()

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == _SETTING_VALUE
