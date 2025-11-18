"""Shared helpers for controller tests."""

# ruff: noqa: S101

from __future__ import annotations

import dataclasses as dc
import os
import shlex
import subprocess
import typing as t

from tests.helpers.parameters import decode_placeholders

RUN_TIMEOUT_SECONDS = 30

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from cmd_mox.controller import CmdMox
    from cmd_mox.ipc import Invocation


@dc.dataclass(slots=True, frozen=True)
class CommandExecution:
    """Parameters for command execution with stdin and environment."""

    cmd: str
    args: str
    stdin: str
    env_var: str
    env_val: str
    check: bool = True


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


def _should_escape_batch_args(command_path: str) -> bool:
    if os.name != "nt":
        return False
    lower = command_path.lower()
    return lower.endswith(".cmd") or lower.endswith(".bat")


def escape_windows_batch_args(argv: list[str]) -> list[str]:
    """Return argv with carets escaped when invoking Windows batch files."""
    if not argv or not _should_escape_batch_args(argv[0]):
        return argv
    escaped = [argv[0]]
    escaped.extend(arg.replace("^", "^^") for arg in argv[1:])
    return escaped


def _execute_command_with_params(
    params: CommandExecution,
) -> subprocess.CompletedProcess[str]:
    """Execute a command described by *params*."""
    env = os.environ | {params.env_var: params.env_val}
    decoded_args = decode_placeholders(params.args)
    argv = [params.cmd, *shlex.split(decoded_args)]
    argv = escape_windows_batch_args(argv)
    return subprocess.run(  # noqa: S603
        argv,
        input=params.stdin,
        capture_output=True,
        text=True,
        check=params.check,
        shell=False,
        env=env,
        timeout=RUN_TIMEOUT_SECONDS,
    )


def execute_command_with_details(
    mox: CmdMox, execution: CommandExecution
) -> subprocess.CompletedProcess[str]:
    """Run the command specified by *execution*."""
    del mox
    return _execute_command_with_params(execution)


def _find_matching_journal_entry(
    mox: CmdMox, expectation: JournalEntryExpectation
) -> Invocation:
    """Locate the journal entry matching *expectation*."""
    candidates = [inv for inv in mox.journal if inv.command == expectation.cmd]
    if expectation.args is not None:
        decoded = decode_placeholders(expectation.args)
        wanted_args = shlex.split(decoded)
        candidates = [inv for inv in candidates if inv.args == wanted_args]
    inv = candidates[-1] if candidates else None
    if inv is None:
        available = [(i.command, list(i.args)) for i in mox.journal]
        msg = (
            f"Journal does not contain expected entry for {expectation.cmd!r} "
            f"with args {expectation.args!r}. Available: {available!r}"
        )
        raise AssertionError(msg)
    return inv


def _validate_journal_entry_fields(
    inv: Invocation, expectation: JournalEntryExpectation
) -> None:
    """Validate stdin, stdout, stderr, and exit_code fields."""
    checks = {
        "stdin": expectation.stdin,
        "stdout": expectation.stdout,
        "stderr": expectation.stderr,
        "exit_code": expectation.exit_code,
    }
    for field, expected in checks.items():
        if expected is not None:
            actual = getattr(inv, field)
            assert actual == expected, f"{field} mismatch: {actual!r} != {expected!r}"


def _validate_journal_entry_environment(
    inv: Invocation, expectation: JournalEntryExpectation
) -> None:
    """Validate environment variable against expectation."""
    if expectation.env_var is not None:
        actual_env = inv.env.get(expectation.env_var)
        assert actual_env == expectation.env_val, (
            f"env[{expectation.env_var!r}] mismatch: "
            f"{actual_env!r} != {expectation.env_val!r}"
        )


def _verify_journal_entry_with_expectation(
    mox: CmdMox, expectation: JournalEntryExpectation
) -> None:
    """Assert journal entry for *expectation.cmd* matches provided expectation."""
    inv = _find_matching_journal_entry(mox, expectation)
    _validate_journal_entry_fields(inv, expectation)
    _validate_journal_entry_environment(inv, expectation)


def verify_journal_entry_details(
    mox: CmdMox, expectation: JournalEntryExpectation
) -> None:
    """Public helper to verify journal entry details."""
    _verify_journal_entry_with_expectation(mox, expectation)
