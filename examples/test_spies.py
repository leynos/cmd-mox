"""Example tests demonstrating spies."""

from __future__ import annotations

import shutil
import subprocess
import typing as t

pytest_plugins = ("cmd_mox.pytest_plugin",)

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from cmd_mox.controller import CmdMox


def _resolve_command(name: str) -> str:
    """Return an absolute path for *name* when available."""
    return shutil.which(name) or name


def test_spy_records_invocations_for_assertions(cmd_mox: CmdMox) -> None:
    """Spies record calls for later inspection and assertions."""
    spy = cmd_mox.spy("whoami").returns(stdout="tester").times_called(1)

    result = subprocess.run(  # noqa: S603 - command path derives from the shim setup
        [_resolve_command("whoami")],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )

    assert result.stdout == "tester"
    assert spy.call_count == 1
    spy.assert_called()
    spy.assert_called_with()
