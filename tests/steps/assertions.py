"""pytest-bdd assertions that validate controller outcomes."""

from __future__ import annotations

import shlex
import typing as typ

from pytest_bdd import parsers, then

from tests.helpers.parameters import decode_placeholders
from tests.steps.shim_management import _require_replay_shim_dir

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc
    import subprocess

    from cmd_mox.controller import CmdMox
    from cmd_mox.errors import MissingEnvironmentError, VerificationError
    from cmd_mox.test_doubles import CommandDouble


def _require_double(
    doubles: cabc.Mapping[str, CommandDouble], cmd: str, kind: str
) -> CommandDouble:
    """Return the required command double, failing clearly when absent."""  # ruff: ignore[docstring-missing-returns] - private assertion helper has an obvious CommandDouble return
    assert cmd in doubles, f"{kind} for command '{cmd}' not found"
    return doubles[cmd]


def _require_spy(mox: CmdMox, cmd: str) -> CommandDouble:
    """Return the registered spy for *cmd*, failing clearly when absent."""  # ruff: ignore[docstring-missing-returns] - private wrapper has an obvious CommandDouble return
    return _require_double(mox.spies, cmd, "Spy")


def _require_mock(mox: CmdMox, cmd: str) -> CommandDouble:
    """Return the registered mock for *cmd*, failing clearly when absent."""  # ruff: ignore[docstring-missing-returns] - private wrapper has an obvious CommandDouble return
    return _require_double(mox.mocks, cmd, "Mock")


@then(parsers.cfparse('the shim for "{cmd}" should end with "{suffix}"'))
def check_shim_suffix(mox: CmdMox, cmd: str, suffix: str) -> None:
    """Ensure the generated shim filename ends with *suffix*."""
    shim_dir = _require_replay_shim_dir(mox)
    matches = sorted(shim_dir.glob(f"{cmd}*"))
    assert matches, f"no shim generated for {cmd}"
    assert matches[0].name.endswith(suffix), (
        f"shim {matches[0].name} does not end with {suffix}"
    )


@then(parsers.cfparse('the output should be "{text}"'))
def check_output(result: subprocess.CompletedProcess[str], text: str) -> None:
    """Ensure the command output matches."""
    assert result.stdout.strip() == text, "Assertion failed"


@then(parsers.cfparse("the exit code should be {code:d}"))
def check_exit_code(result: subprocess.CompletedProcess[str], code: int) -> None:
    """Assert the process exit code equals *code*."""
    assert result.returncode == code, "Assertion failed"


@then(parsers.cfparse('the stderr should contain "{text}"'))
def check_stderr(result: subprocess.CompletedProcess[str], text: str) -> None:
    """Ensure standard error output contains *text*."""
    assert text in result.stderr, "Assertion failed"


@then(parsers.cfparse('the verification error message should contain "{text}"'))
def verification_error_contains(
    verification_error: VerificationError, text: str
) -> None:
    """Assert the captured verification error contains *text*."""
    assert text in str(verification_error), "Assertion failed"


@then(parsers.cfparse('the replay error message should contain "{text}"'))
def replay_error_contains(replay_error: MissingEnvironmentError, text: str) -> None:
    """Assert the captured replay error contains *text*."""
    assert text in str(replay_error), "Assertion failed"


@then(parsers.cfparse('the verification error message should not contain "{text}"'))
def verification_error_excludes(
    verification_error: VerificationError, text: str
) -> None:
    """Assert the captured verification error omits *text*."""
    assert text not in str(verification_error), "Assertion failed"


@then(parsers.cfparse('the spy "{cmd}" should record {count:d} invocation'))
def check_spy(mox: CmdMox, cmd: str, count: int) -> None:
    """Verify the spy recorded the invocation."""
    spy = _require_spy(mox, cmd)
    assert len(spy.invocations) == count, "Assertion failed"


@then(parsers.cfparse('the spy "{cmd}" call count should be {count:d}'))
def check_spy_call_count(mox: CmdMox, cmd: str, count: int) -> None:
    """Assert ``SpyCommand.call_count`` equals *count*."""
    spy = _require_spy(mox, cmd)
    assert spy.call_count == count, "Assertion failed"


@then(parsers.cfparse('the spy "{cmd}" should have been called'))
def spy_assert_called(mox: CmdMox, cmd: str) -> None:
    """Assert the spy was invoked at least once."""
    _require_spy(mox, cmd).assert_called()


@then(
    parsers.cfparse('the spy "{cmd}" should have been called with arguments "{args}"')
)
def spy_assert_called_with(mox: CmdMox, cmd: str, args: str) -> None:
    """Assert the spy's last call used the given arguments."""
    decoded = decode_placeholders(args)
    _require_spy(mox, cmd).assert_called_with(*shlex.split(decoded))


@then(parsers.cfparse('the spy "{cmd}" should not have been called'))
def spy_assert_not_called(mox: CmdMox, cmd: str) -> None:
    """Assert the spy was never invoked."""
    _require_spy(mox, cmd).assert_not_called()


@then(parsers.cfparse('the mock "{cmd}" should record {count:d} invocation'))
def check_mock(mox: CmdMox, cmd: str, count: int) -> None:
    """Verify the mock recorded the invocation."""
    mock = _require_mock(mox, cmd)
    assert len(mock.invocations) == count, "Assertion failed"
