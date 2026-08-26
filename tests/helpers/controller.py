"""Shared helpers for controller tests."""

from __future__ import annotations

import dataclasses as dc
import os
import shlex
import shutil
import subprocess
import typing as typ

from tests.helpers.parameters import decode_placeholders

RUN_TIMEOUT_SECONDS = 30

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
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
    """Return True when *command_path* resolves to a batch script.

    On Windows the shell treats ``.cmd``/``.bat`` files specially and consumes
    one layer of caret escaping before our shim sees the arguments. We need to
    double carets whenever the resolved target is a batch file, even if the
    caller omitted the extension and relies on ``PATHEXT`` to find the shim.

    Returns
    -------
    bool
        Whether the command path invokes a Windows batch script.
    """
    if os.name != "nt":
        return False

    lower = command_path.lower()
    if lower.endswith((".cmd", ".bat")):
        return True

    resolved = shutil.which(command_path)
    return bool(resolved and resolved.lower().endswith((".cmd", ".bat")))


def escape_windows_batch_args(argv: list[str]) -> list[str]:
    """Return argv with carets doubled when invoking Windows batch files.

    Carets are doubled because when subprocess.run invokes a .cmd file on
    Windows, cmd.exe consumes one layer of escaping. Argument quoting is handled
    by subprocess itself, so no manual quoting is required here.

    Note: batch parsing happens twice for our shim flow (once when invoking the
    launcher and again when the launcher expands ``%*``). To preserve a literal
    caret in the final Python argv we must therefore quadruple it up-front.

    Returns
    -------
    list[str]
        The original arguments, with batch-file carets escaped when necessary.
    """
    if not argv or not _should_escape_batch_args(argv[0]):
        return argv
    escaped = [argv[0]]
    escaped.extend(arg.replace("^", "^^^^") if "^" in arg else arg for arg in argv[1:])
    return escaped


def _execute_command_with_params(
    params: CommandExecution,
) -> subprocess.CompletedProcess[str]:
    """Execute a command described by *params*."""  # ruff: ignore[docstring-missing-returns] - test-only helper; return contract is local
    env = os.environ | {params.env_var: params.env_val}
    decoded_args = decode_placeholders(params.args)
    argv = [params.cmd, *shlex.split(decoded_args)]
    argv = escape_windows_batch_args(argv)
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - the test executes a command path prepared by the test harness
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
    """Run the command specified by *execution*."""  # ruff: ignore[docstring-missing-returns] - test-only helper; return contract is local
    del mox
    return _execute_command_with_params(execution)


def _find_matching_journal_entry(
    mox: CmdMox, expectation: JournalEntryExpectation
) -> Invocation:
    """Locate the journal entry matching *expectation*."""  # ruff: ignore[docstring-missing-returns, docstring-missing-exception] - test-only helper; contract and failure are local
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
            assert actual == expected, (  # ruff: ignore[assert] - assertion helpers make BDD failures concise and local
                f"{field} mismatch: {actual!r} != {expected!r}"
            )


def _validate_journal_entry_environment(
    inv: Invocation, expectation: JournalEntryExpectation
) -> None:
    """Validate environment variable against expectation."""
    if expectation.env_var is not None:
        actual_env = inv.env.get(expectation.env_var)
        assert actual_env == expectation.env_val, (  # ruff: ignore[assert] - assertion helpers make BDD failures concise and local
            f"env[{expectation.env_var!r}] mismatch: "
            f"{actual_env!r} != {expectation.env_val!r}"
        )


def verify_journal_entry_details(
    mox: CmdMox, expectation: JournalEntryExpectation
) -> None:
    """Assert that the latest matching journal entry meets an expectation.

    Parameters
    ----------
    mox : CmdMox
        Controller whose journal is inspected.
    expectation : JournalEntryExpectation
        Expected command and optional invocation details.

    """
    inv = _find_matching_journal_entry(mox, expectation)
    _validate_journal_entry_fields(inv, expectation)
    _validate_journal_entry_environment(inv, expectation)
