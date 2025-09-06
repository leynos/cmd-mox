"""Shared helpers for controller tests."""

# ruff: noqa: S101

from __future__ import annotations

import dataclasses as dc
import os
import shlex
import subprocess
import typing as t

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from cmd_mox.controller import CmdMox


@dc.dataclass(slots=True, frozen=True)
class CommandExecution:
    """Parameters for command execution with stdin and environment."""

    cmd: str
    args: str
    stdin: str
    env_var: str
    env_val: str


@dc.dataclass(slots=True, frozen=True)
class JournalEntryExpectation:
    """Expected details for a journal entry."""

    cmd: str
    args: str | None = None
    stdin: str | None = None
    env_var: str | None = None
    env_val: str | None = None
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None


def _execute_command_with_params(
    params: CommandExecution,
) -> subprocess.CompletedProcess[str]:
    """Execute a command described by *params*."""
    env = os.environ | {params.env_var: params.env_val}
    argv = [params.cmd, *shlex.split(params.args)]
    return subprocess.run(  # noqa: S603
        argv,
        input=params.stdin,
        capture_output=True,
        text=True,
        check=True,
        shell=False,
        env=env,
        timeout=30,
    )


def execute_command_with_details(
    mox: CmdMox, execution: CommandExecution
) -> subprocess.CompletedProcess[str]:
    """Run the command specified by *execution*."""
    del mox
    return _execute_command_with_params(execution)


def _verify_journal_entry_with_expectation(
    mox: CmdMox, expectation: JournalEntryExpectation
) -> None:
    """Assert journal entry for *expectation.cmd* matches provided expectation."""
    inv = next((inv for inv in mox.journal if inv.command == expectation.cmd), None)
    assert inv is not None, f"Journal does not contain command: {expectation.cmd!r}"
    if expectation.args is not None:
        assert list(inv.args) == shlex.split(expectation.args), (
            f"args mismatch: {list(inv.args)!r} != {shlex.split(expectation.args)!r}"
        )
    if expectation.stdin is not None:
        assert inv.stdin == expectation.stdin, (
            f"stdin mismatch: {inv.stdin!r} != {expectation.stdin!r}"
        )
    if expectation.env_var is not None:
        assert inv.env.get(expectation.env_var) == expectation.env_val, (
            f"env[{expectation.env_var!r}] mismatch: "
            f"{inv.env.get(expectation.env_var)!r} != {expectation.env_val!r}"
        )
    if expectation.stdout is not None:
        assert inv.stdout == expectation.stdout, (
            f"stdout mismatch: {inv.stdout!r} != {expectation.stdout!r}"
        )
    if expectation.stderr is not None:
        assert inv.stderr == expectation.stderr, (
            f"stderr mismatch: {inv.stderr!r} != {expectation.stderr!r}"
        )
    if expectation.exit_code is not None:
        assert inv.exit_code == expectation.exit_code, (
            f"exit_code mismatch: {inv.exit_code!r} != {expectation.exit_code!r}"
        )


def verify_journal_entry_details(
    mox: CmdMox, expectation: JournalEntryExpectation
) -> None:
    """Public helper to verify journal entry details."""
    _verify_journal_entry_with_expectation(mox, expectation)
