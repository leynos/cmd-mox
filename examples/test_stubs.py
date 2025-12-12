"""Example tests demonstrating stub usage."""

from __future__ import annotations

import subprocess
import typing as t

from examples._utils import resolve_command

pytest_plugins = ("cmd_mox.pytest_plugin",)

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from cmd_mox.controller import CmdMox
    from cmd_mox.ipc import Invocation


def test_stub_returns_canned_stdout(cmd_mox: CmdMox) -> None:
    """Stubs provide canned responses without strict verification."""
    cmd_mox.stub("hello").returns(stdout="world")

    result = subprocess.run(  # noqa: S603 - command path derives from the shim setup
        [resolve_command("hello")],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )

    assert result.stdout == "world"


def test_stub_runs_dynamic_handler(cmd_mox: CmdMox) -> None:
    """Stubs can run a dynamic handler to compute output."""

    def handler(invocation: Invocation) -> tuple[str, str, int]:
        if not invocation.args:
            return ("", "missing argument", 1)
        return (f"hi {invocation.args[0]}", "", 0)

    cmd_mox.stub("greeter").with_args("CmdMox").runs(handler)

    result = subprocess.run(  # noqa: S603 - command path derives from the shim setup
        [resolve_command("greeter"), "CmdMox"],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )

    assert result.stdout == "hi CmdMox"
