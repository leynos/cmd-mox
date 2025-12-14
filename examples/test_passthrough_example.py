"""Example tests demonstrating passthrough spies."""

from __future__ import annotations

import subprocess
import sys
import typing as t

from examples._utils import resolve_command

pytest_plugins = ("cmd_mox.pytest_plugin",)

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    import pytest

    from cmd_mox.controller import CmdMox


def test_passthrough_spy_executes_real_command(
    cmd_mox: CmdMox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Passthrough spies run the real command while recording invocations."""
    command_name = "realcmd"
    monkeypatch.setenv(f"CMOX_REAL_COMMAND_{command_name}", sys.executable)
    spy = cmd_mox.spy(command_name).passthrough()

    result = subprocess.run(  # noqa: S603 - command path derives from the shim setup
        [resolve_command(command_name), "-c", "print('hello')"],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )

    assert result.stdout.strip() == "hello"
    assert spy.call_count == 1
    spy.assert_called_with("-c", "print('hello')")
