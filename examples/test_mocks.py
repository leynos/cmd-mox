"""Example tests demonstrating strict mocks."""

from __future__ import annotations

import subprocess
import typing as t

from cmd_mox.comparators import StartsWith
from examples._utils import resolve_command

pytest_plugins = ("cmd_mox.pytest_plugin",)

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from cmd_mox.controller import CmdMox


def test_mock_enforces_args_and_call_count(cmd_mox: CmdMox) -> None:
    """Mocks require exact arguments and can enforce call counts."""
    cmd_mox.mock("git").with_args("status").returns(stdout="ok").times(2)

    first = subprocess.run(  # noqa: S603 - command path derives from the shim setup
        [resolve_command("git"), "status"],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    second = subprocess.run(  # noqa: S603 - command path derives from the shim setup
        [resolve_command("git"), "status"],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )

    assert first.stdout == "ok"
    assert second.stdout == "ok"


def test_mock_with_matching_args(cmd_mox: CmdMox) -> None:
    """Mocks can use comparators for flexible argument matching."""
    cmd_mox.mock("curl").with_matching_args(StartsWith("--url=")).returns(
        stdout="fetched"
    )

    result = subprocess.run(  # noqa: S603 - command path derives from the shim setup
        [resolve_command("curl"), "--url=https://example.com"],
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )

    assert result.stdout == "fetched"
