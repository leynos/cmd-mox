"""pytest-bdd steps that execute shims and commands."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import textwrap
import typing as t

from pytest_bdd import parsers, then, when

from tests.helpers.controller import (
    CommandExecution,
    escape_windows_batch_args,
    execute_command_with_details,
)
from tests.helpers.parameters import CommandInputs, EnvVar, decode_placeholders

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from cmd_mox.controller import CmdMox


def _try_resolve_windows_cmd(cmd: str) -> str | None:
    """Try to resolve a .cmd variant on Windows if not already a .cmd file."""
    if os.name != "nt":
        return None
    if cmd.lower().endswith(".cmd"):
        return None
    return shutil.which(f"{cmd}.cmd")


def _resolve_command(cmd: str) -> str:
    """Return an executable path for *cmd*, respecting PATHEXT on Windows."""
    if resolved := shutil.which(cmd):
        return resolved
    if windows_cmd := _try_resolve_windows_cmd(cmd):
        return windows_cmd
    return cmd


def _run(argv: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    """Execute *argv* with consistent subprocess settings.

    Parameters
    ----------
    argv : list[str]
        Command invocation, including arguments.
    check : bool
        When True, raise :class:`CalledProcessError` for non-zero exits.
    """
    argv = escape_windows_batch_args(argv)
    return subprocess.run(  # noqa: S603
        argv,
        capture_output=True,
        text=True,
        check=check,
        shell=False,
    )


@when(parsers.cfparse('I run the command "{cmd}"'), target_fixture="result")
def run_command(mox: CmdMox, cmd: str) -> subprocess.CompletedProcess[str]:
    """Invoke the stubbed command."""
    resolved = _resolve_command(cmd)
    return _run([resolved], check=True)


@then(parsers.cfparse('I run the command "{cmd}"'), target_fixture="result")
def then_run_command(
    mox: CmdMox, cmd: str
) -> subprocess.CompletedProcess[str]:  # pragma: no cover - pytest-bdd glue
    """Alias the 'When' step for scenarios that use Then/And."""
    return run_command(mox, cmd)


@when(
    parsers.cfparse('I run the command "{cmd}" expecting failure'),
    target_fixture="result",
)
def run_command_failure(cmd: str) -> subprocess.CompletedProcess[str]:
    """Run *cmd* expecting a non-zero exit status."""
    return _run([_resolve_command(cmd)], check=False)


@when(
    parsers.cfparse('I run the command "{cmd}" with arguments "{args}"'),
    target_fixture="result",
)
def run_command_args(
    mox: CmdMox,
    cmd: str,
    args: str,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* with additional arguments."""
    decoded = decode_placeholders(args)
    argv = [_resolve_command(cmd), *shlex.split(decoded)]
    return _run(argv, check=True)


def _resolve_empty_placeholder(value: str) -> str:
    """Resolve the special '<empty>' placeholder to an empty string."""
    return "" if value == "<empty>" else value


def _run_command_args_stdin_env_impl(
    mox: CmdMox,
    cmd: str,
    inputs: CommandInputs,
    env: EnvVar,
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* using aggregated command inputs and environment."""
    params = CommandExecution(
        cmd=cmd,
        args=inputs.args,
        stdin=inputs.stdin,
        env_var=env.name,
        env_val=env.value,
    )
    return execute_command_with_details(mox, params)


@when(
    parsers.cfparse(
        'I run the command "{cmd}" with arguments "{args}" '
        'using stdin "{stdin}" and env var "{var}"="{val}"'
    ),
    target_fixture="result",
)
def run_command_args_stdin_env(
    mox: CmdMox,
    cmd: str,
    args: str,
    stdin: str,
    var: str,
    val: str,
) -> subprocess.CompletedProcess[str]:  # noqa: PLR0913, RUF100 - pytest-bdd step wrapper requires all parsed params
    """Run *cmd* with arguments, stdin, and an environment variable."""
    resolved_args = _resolve_empty_placeholder(args)
    resolved_stdin = _resolve_empty_placeholder(stdin)
    decoded_args = decode_placeholders(resolved_args)
    inputs = CommandInputs(args=decoded_args, stdin=resolved_stdin)
    env = EnvVar(name=var, value=val)
    return _run_command_args_stdin_env_impl(mox, cmd, inputs, env)


@when(
    parsers.cfparse('I run the command "{cmd}" using a with block'),
    target_fixture="result",
)
def run_command_with_block(mox: CmdMox, cmd: str) -> subprocess.CompletedProcess[str]:
    """Run *cmd* inside a ``with mox`` block and verify afterwards."""
    original_env = os.environ.copy()
    with mox:
        mox.replay()
        result = _run([_resolve_command(cmd)], check=True)
    assert os.environ == original_env
    return result


@when(
    parsers.cfparse('I run the shim sequence "{sequence}"'),
    target_fixture="result",
)
def run_shim_sequence(sequence: str) -> subprocess.CompletedProcess[str]:
    """Invoke a list of shim commands within a single Python process."""
    commands = shlex.split(sequence)
    script = textwrap.dedent(
        """
        import contextlib
        import io
        import sys

        import cmd_mox.shim as shim

        def invoke(name: str) -> tuple[str, str, int]:
            original_argv = sys.argv[:]
            original_stdin = sys.stdin
            stdout = io.StringIO()
            stderr = io.StringIO()
            sys.argv = [name]
            sys.stdin = io.StringIO("")
            try:
                with contextlib.redirect_stdout(stdout):
                    with contextlib.redirect_stderr(stderr):
                        try:
                            shim.main()
                        except SystemExit as exc:
                            code = exc.code
                            if code is None:
                                code = 0
                            elif not isinstance(code, int):
                                code = 1
                        else:
                            code = 0
            finally:
                sys.argv = original_argv
                sys.stdin = original_stdin
            return stdout.getvalue(), stderr.getvalue(), code

        last_stdout = ""
        last_stderr = ""
        code = 0
        for cmd_name in sys.argv[1:]:
            last_stdout, last_stderr, code = invoke(cmd_name)
            if code != 0:
                break

        sys.stdout.write(last_stdout)
        sys.stderr.write(last_stderr)
        sys.exit(code)
        """
    )
    argv = [sys.executable, "-c", script, *commands]
    return subprocess.run(argv, capture_output=True, text=True, check=True, shell=False)  # noqa: S603
