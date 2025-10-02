"""Behavioural tests for CmdMox controller using pytest-bdd."""

from __future__ import annotations

import contextlib
import os
import shlex
import subprocess
import sys
import textwrap
import typing as t
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, scenario, then, when

from cmd_mox.comparators import Any, Contains, IsA, Predicate, Regex, StartsWith
from cmd_mox.controller import CmdMox
from cmd_mox.environment import CMOX_REAL_COMMAND_ENV_PREFIX
from cmd_mox.errors import (
    UnexpectedCommandError,
    UnfulfilledExpectationError,
    VerificationError,
)
from cmd_mox.ipc import Response
from tests.helpers.controller import (
    CommandExecution,
    JournalEntryExpectation,
    execute_command_with_details,
    verify_journal_entry_details,
)

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from cmd_mox.ipc import Invocation


FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"

_ERROR_TYPES: dict[str, type[VerificationError]] = {
    "UnexpectedCommandError": UnexpectedCommandError,
    "UnfulfilledExpectationError": UnfulfilledExpectationError,
    "VerificationError": VerificationError,
}


@given("a CmdMox controller", target_fixture="mox")
def create_controller() -> CmdMox:
    """Create a fresh CmdMox instance."""
    return CmdMox()


@given(
    parsers.cfparse("a CmdMox controller with max journal size {size:d}"),
    target_fixture="mox",
)
def create_controller_with_limit(size: int) -> CmdMox:
    """Create a CmdMox instance with bounded journal."""
    return CmdMox(max_journal_entries=size)


@given(
    parsers.cfparse("creating a CmdMox controller with max journal size {size:d} fails")
)
def create_controller_with_limit_fails(size: int) -> None:
    """Assert constructing a controller with invalid journal size fails."""
    with pytest.raises(ValueError, match="max_journal_entries must be positive"):
        CmdMox(max_journal_entries=size)


@given(parsers.cfparse('the command "{cmd}" is stubbed to return "{text}"'))
def stub_command(mox: CmdMox, cmd: str, text: str) -> None:
    """Configure a stubbed command."""
    mox.stub(cmd).returns(stdout=text)


@given(
    parsers.cfparse(
        'the command "{cmd}" is stubbed to return stdout "{stdout}" '
        'stderr "{stderr}" exit code {code:d}'
    )
)
def stub_command_full(
    mox: CmdMox, cmd: str, stdout: str, stderr: str, code: int
) -> None:
    """Configure a stubbed command with explicit streams and exit code."""
    mox.stub(cmd).returns(stdout=stdout, stderr=stderr, exit_code=code)


@given(parsers.cfparse('the command "{cmd}" is mocked to return "{text}"'))
def mock_command(mox: CmdMox, cmd: str, text: str) -> None:
    """Configure a mocked command."""
    mox.mock(cmd).returns(stdout=text)


@given(
    parsers.cfparse(
        'the command "{cmd}" is mocked to return "{text}" with comparator args'
    )
)
def mock_with_comparator_args(mox: CmdMox, cmd: str, text: str) -> None:
    """Mock command using various comparators for argument matching."""
    mox.mock(cmd).with_matching_args(
        Any(),
        IsA(int),
        Regex(r"^foo\d+$"),
        Contains("bar"),
        StartsWith("baz"),
        Predicate(str.isupper),
    ).returns(stdout=text)


@given(
    parsers.re(
        r'the command "(?P<cmd>[^"]+)" is mocked to return "(?P<text>[^"]+)" '
        r"times (?P<count>\d+)"
    )
)
def mock_command_times(mox: CmdMox, cmd: str, text: str, count: str) -> None:
    """Configure a mocked command with an expected call count using times()."""
    expectation = mox.mock(cmd).returns(stdout=text)
    expectation.times(int(count))


@given(
    parsers.re(
        r'the command "(?P<cmd>[^"]+)" is mocked to return "(?P<text>[^"]+)" '
        r"times called (?P<count>\d+)"
    )
)
def mock_command_times_called(mox: CmdMox, cmd: str, text: str, count: str) -> None:
    """Configure a mocked command with an expected call count using times_called()."""
    expectation = mox.mock(cmd).returns(stdout=text)
    expectation.times_called(int(count))


@given(parsers.cfparse('the command "{cmd}" is spied to return "{text}"'))
def spy_command(mox: CmdMox, cmd: str, text: str) -> None:
    """Configure a spied command."""
    mox.spy(cmd).returns(stdout=text)


@given(parsers.cfparse('the command "{cmd}" is spied to passthrough'))
def spy_passthrough(mox: CmdMox, cmd: str) -> None:
    """Configure a passthrough spy."""
    mox.spy(cmd).passthrough()


@given(parsers.cfparse('the command "{cmd}" is stubbed to run a handler'))
def stub_runs(mox: CmdMox, cmd: str) -> None:
    """Configure a stub with a dynamic handler."""

    def handler(invocation: Invocation) -> tuple[str, str, int]:
        assert invocation.command == cmd
        return ("handled", "", 0)

    mox.stub(cmd).runs(handler)


@given(
    parsers.cfparse(
        'the command "{cmd}" is mocked with args "{args}" returning "{text}" in order'
    )
)
def mock_with_args_in_order(mox: CmdMox, cmd: str, args: str, text: str) -> None:
    """Configure an ordered mock with arguments."""
    mox.mock(cmd).with_args(*shlex.split(args)).returns(stdout=text).in_order()


@given(
    parsers.cfparse(
        'the command "{cmd}" is mocked with args "{args}" returning "{text}" any order'
    )
)
def mock_with_args_any_order(mox: CmdMox, cmd: str, args: str, text: str) -> None:
    """Configure an unordered mock with arguments."""
    mox.mock(cmd).with_args(*shlex.split(args)).returns(stdout=text).any_order()


@given(parsers.cfparse('the command "{cmd}" is stubbed with env var "{var}"="{val}"'))
def stub_with_env(mox: CmdMox, cmd: str, var: str, val: str) -> None:
    """Stub command that outputs an injected env variable."""

    def handler(invocation: Invocation) -> tuple[str, str, int]:
        return (os.environ.get(var, ""), "", 0)

    mox.stub(cmd).with_env({var: val}).runs(handler)


@given(parsers.cfparse('the command "{cmd}" seeds shim env var "{var}"="{val}"'))
def stub_seeds_shim_env(mox: CmdMox, cmd: str, var: str, val: str) -> None:
    """Stub command that injects an environment override for future shims."""

    def handler(_: Invocation) -> Response:
        return Response(env={var: val})

    mox.stub(cmd).runs(handler)


@given(
    parsers.cfparse(
        'the command "{cmd}" expects shim env var "{expected}"="{value}" '
        'and seeds "{var}"="{val}"'
    )
)
def stub_expect_and_seed_env(
    mox: CmdMox, cmd: str, expected: str, value: str, var: str, val: str
) -> None:
    """Stub that validates an inherited env var before injecting another."""

    def handler(invocation: Invocation) -> Response:
        actual = invocation.env.get(expected)
        if actual != value:
            msg = (
                f"expected shim env {expected!r} to equal {value!r} but got {actual!r}"
            )
            raise AssertionError(msg)
        return Response(env={var: val})

    mox.stub(cmd).runs(handler)


@given(
    parsers.cfparse(
        'the command "{cmd}" records shim env vars "{first}"="{first_val}" '
        'and "{second}"="{second_val}"'
    )
)
def stub_records_merged_env(
    mox: CmdMox, cmd: str, first: str, first_val: str, second: str, second_val: str
) -> None:
    """Stub that asserts merged shim environment values."""

    def handler(invocation: Invocation) -> tuple[str, str, int]:
        actual_first = invocation.env.get(first)
        actual_second = invocation.env.get(second)
        if actual_first != first_val:
            msg = (
                "expected shim env "
                f"{first!r} to equal {first_val!r} but got {actual_first!r}"
            )
            raise AssertionError(msg)
        if actual_second != second_val:
            msg = (
                "expected shim env "
                f"{second!r} to equal {second_val!r} but got {actual_second!r}"
            )
            raise AssertionError(msg)
        return (f"{actual_first}+{actual_second}", "", 0)

    mox.stub(cmd).runs(handler)


@given(parsers.cfparse('the command "{cmd}" requires env var "{var}"="{val}"'))
def command_requires_env(mox: CmdMox, cmd: str, var: str, val: str) -> None:
    """Attach an environment requirement to an existing double."""
    for collection in (mox.mocks, mox.stubs, mox.spies):
        double = collection.get(cmd)
        if double is not None:
            double.expectation.with_env({var: val})
            return

    msg = f"Command {cmd!r} has not been registered"
    raise AssertionError(msg)


@given(parsers.cfparse('the command "{cmd}" resolves to a non-executable file'))
def non_executable_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cmd: str,
) -> None:
    """Place a non-executable *cmd* earlier in ``PATH`` for passthrough tests."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    dummy = bin_dir / cmd
    dummy.write_text("#!/bin/sh\necho hi\n")
    dummy.chmod(0o644)

    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{original_path}")
    monkeypatch.setenv(f"{CMOX_REAL_COMMAND_ENV_PREFIX}{cmd}", str(dummy))


@given(parsers.cfparse('the command "{cmd}" will timeout'))
def command_will_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cmd: str,
) -> None:
    """Return a deterministic timeout-like response for *cmd*."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / cmd
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('timeout after 30 seconds\\n')\n"
        "sys.exit(124)\n"
    )
    script.chmod(0o755)

    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{original_path}")
    monkeypatch.setenv(f"{CMOX_REAL_COMMAND_ENV_PREFIX}{cmd}", str(script))


@when("I replay the controller", target_fixture="mox_stack")
def replay_controller(mox: CmdMox) -> contextlib.ExitStack:
    """Enter replay mode within a context manager."""
    stack = contextlib.ExitStack()
    stack.enter_context(mox)
    mox.replay()
    return stack


@when(parsers.cfparse('I run the command "{cmd}"'), target_fixture="result")
def run_command(mox: CmdMox, cmd: str) -> subprocess.CompletedProcess[str]:
    """Invoke the stubbed command."""
    return subprocess.run(  # noqa: S603
        [cmd], capture_output=True, text=True, check=True, shell=False
    )


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


@when(
    parsers.cfparse('I run the command "{cmd}" expecting failure'),
    target_fixture="result",
)
def run_command_failure(cmd: str) -> subprocess.CompletedProcess[str]:
    """Run *cmd* expecting a non-zero exit status."""
    return subprocess.run(  # noqa: S603
        [cmd], capture_output=True, text=True, check=False, shell=False
    )


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
    argv = [cmd, *shlex.split(args)]
    return subprocess.run(argv, capture_output=True, text=True, check=True, shell=False)  # noqa: S603


@when("I verify the controller")
def verify_controller(mox: CmdMox, mox_stack: contextlib.ExitStack) -> None:
    """Invoke verification and close context."""
    mox.verify()
    mox_stack.close()


@when(
    parsers.cfparse("I verify the controller expecting an {error_name}"),
    target_fixture="verification_error",
)
def verify_controller_expect_error(
    mox: CmdMox, mox_stack: contextlib.ExitStack, error_name: str
) -> VerificationError:
    """Invoke verification expecting a specific error type."""
    error_type = _ERROR_TYPES.get(error_name)
    if error_type is None:  # pragma: no cover - invalid feature configuration
        msg = f"Unknown verification error type: {error_name}"
        raise ValueError(msg)
    try:
        with pytest.raises(error_type) as excinfo:
            mox.verify()
    finally:
        mox_stack.close()
    return t.cast("VerificationError", excinfo.value)


@when(
    parsers.cfparse('I run the command "{cmd}" using a with block'),
    target_fixture="result",
)
def run_command_with_block(mox: CmdMox, cmd: str) -> subprocess.CompletedProcess[str]:
    """Run *cmd* inside a ``with mox`` block and verify afterwards."""
    original_env = os.environ.copy()
    with mox:
        mox.replay()
        result = subprocess.run(  # noqa: S603
            [cmd], capture_output=True, text=True, check=True, shell=False
        )
    assert os.environ == original_env
    return result


@then(parsers.cfparse('the output should be "{text}"'))
def check_output(result: subprocess.CompletedProcess[str], text: str) -> None:
    """Ensure the command output matches."""
    assert result.stdout.strip() == text


@then(parsers.cfparse("the exit code should be {code:d}"))
def check_exit_code(result: subprocess.CompletedProcess[str], code: int) -> None:
    """Assert the process exit code equals *code*."""
    assert result.returncode == code


@then(parsers.cfparse('the verification error message should contain "{text}"'))
def verification_error_contains(
    verification_error: VerificationError, text: str
) -> None:
    """Assert the captured verification error contains *text*."""
    assert text in str(verification_error)


@then(parsers.cfparse('the stderr should contain "{text}"'))
def check_stderr(result: subprocess.CompletedProcess[str], text: str) -> None:
    """Ensure standard error output contains *text*."""
    assert text in result.stderr


@then(
    parsers.re(
        r"the journal should contain (?P<count>\d+) "
        r'invocation(?:s)? of "(?P<cmd>[^\"]+)"'
    )
)
def check_journal(mox: CmdMox, count: str, cmd: str) -> None:
    """Verify the journal records *count* invocations of *cmd*."""
    matches = [inv for inv in mox.journal if inv.command == cmd]
    assert len(matches) == int(count)


@then(parsers.cfparse('the spy "{cmd}" should record {count:d} invocation'))
def check_spy(mox: CmdMox, cmd: str, count: int) -> None:
    """Verify the spy recorded the invocation."""
    assert cmd in mox.spies, f"Spy for command '{cmd}' not found"
    spy = mox.spies[cmd]
    assert len(spy.invocations) == count


@then(parsers.cfparse('the spy "{cmd}" call count should be {count:d}'))
def check_spy_call_count(mox: CmdMox, cmd: str, count: int) -> None:
    """Assert ``SpyCommand.call_count`` equals *count*."""
    assert cmd in mox.spies, f"Spy for command '{cmd}' not found"
    spy = mox.spies[cmd]
    assert spy.call_count == count


@then(parsers.cfparse('the spy "{cmd}" should have been called'))
def spy_assert_called(mox: CmdMox, cmd: str) -> None:
    """Assert the spy was invoked at least once."""
    assert cmd in mox.spies, f"Spy for command '{cmd}' not found"
    mox.spies[cmd].assert_called()


@then(
    parsers.cfparse('the spy "{cmd}" should have been called with arguments "{args}"')
)
def spy_assert_called_with(mox: CmdMox, cmd: str, args: str) -> None:
    """Assert the spy's last call used the given arguments."""
    assert cmd in mox.spies, f"Spy for command '{cmd}' not found"
    mox.spies[cmd].assert_called_with(*shlex.split(args))


@then(parsers.cfparse('the spy "{cmd}" should not have been called'))
def spy_assert_not_called(mox: CmdMox, cmd: str) -> None:
    """Assert the spy was never invoked."""
    assert cmd in mox.spies, f"Spy for command '{cmd}' not found"
    mox.spies[cmd].assert_not_called()


@then(parsers.cfparse('the mock "{cmd}" should record {count:d} invocation'))
def check_mock(mox: CmdMox, cmd: str, count: int) -> None:
    """Verify the mock recorded the invocation."""
    assert cmd in mox.mocks, f"Mock for command '{cmd}' not found"
    mock = mox.mocks[cmd]
    assert len(mock.invocations) == count


@then(parsers.cfparse("the journal order should be {commands}"))
def check_journal_order(mox: CmdMox, commands: str) -> None:
    """Ensure the journal entries are in the expected order."""
    expected = commands.split(",")
    actual = [inv.command for inv in mox.journal]
    assert actual == expected


@scenario(str(FEATURES_DIR / "controller.feature"), "stubbed command execution")
def test_stubbed_command_execution() -> None:
    """Stubbed command returns expected output."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "shim forwards stdout stderr and exit code",
)
def test_shim_forwards_streams() -> None:
    """Shim applies server provided stdout, stderr, and exit code."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "shim merges environment overrides across invocations",
)
def test_shim_merges_env_overrides() -> None:
    """Shim persists environment overrides between invocations."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "mocked command execution")
def test_mocked_command_execution() -> None:
    """Mocked command returns expected output."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "spy records invocation")
def test_spy_records_invocation() -> None:
    """Spy records command invocation."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "spy assertion helpers")
def test_spy_assertion_helpers() -> None:
    """Spy exposes assert_called helpers."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "journal preserves invocation order"
)
def test_journal_preserves_order() -> None:
    """Journal records commands in order."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "times alias maps to times_called")
def test_times_alias_maps_to_times_called() -> None:
    """times() and times_called() behave identically in the DSL."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "context manager usage")
def test_context_manager_usage() -> None:
    """CmdMox works within a ``with`` block."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "stub runs dynamic handler")
def test_stub_runs_dynamic_handler() -> None:
    """Stub executes a custom handler."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "ordered mocks match arguments")
def test_ordered_mocks_match_arguments() -> None:
    """Mocks enforce argument matching and ordering."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "environment variables can be injected"
)
def test_environment_injection() -> None:
    """Stub applies environment variables to the shim."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "passthrough spy executes real command"
)
def test_passthrough_spy() -> None:
    """Spy runs the real command while recording."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "passthrough spy handles missing command"
)
def test_passthrough_spy_missing_command() -> None:
    """Spy reports an error when the real command is absent."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "passthrough spy handles permission error"
)
def test_passthrough_spy_permission_error() -> None:
    """Spy records permission errors from the real command."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "passthrough spy handles timeout")
def test_passthrough_spy_timeout() -> None:
    """Spy records timeouts from the real command."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "mock matches arguments with comparators",
)
def test_mock_matches_arguments_with_comparators() -> None:
    """Mocks can use comparator objects for flexible argument matching."""
    pass


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
    params = CommandExecution(cmd=cmd, args=args, stdin=stdin, env_var=var, env_val=val)
    return execute_command_with_details(mox, params)


def _validate_journal_entry_details(
    mox: CmdMox, expectation: JournalEntryExpectation
) -> None:
    """Validate journal entry records invocation details."""
    verify_journal_entry_details(mox, expectation)


@then(
    parsers.cfparse(
        'the journal entry for "{cmd}" should record arguments "{args}" '
        'stdin "{stdin}" env var "{var}"="{val}"'
    )
)
def check_journal_entry_details(  # noqa: PLR0913, RUF100 - pytest-bdd step wrapper requires all parsed params
    mox: CmdMox,
    cmd: str,
    args: str,
    stdin: str,
    var: str,
    val: str,
) -> None:
    """Validate journal entry records invocation details."""
    expectation = JournalEntryExpectation(cmd, args, stdin, var, val)
    _validate_journal_entry_details(mox, expectation)


@then(
    parsers.re(
        r'the journal entry for "(?P<cmd>[^"]+)" should record stdout '
        r'"(?P<stdout>[^"]*)" stderr "(?P<stderr>[^"]*)" exit code (?P<code>\d+)'
    )
)
def check_journal_entry_result(  # noqa: PLR0913, RUF100 - pytest-bdd step wrapper requires all parsed params
    mox: CmdMox,
    cmd: str,
    stdout: str,
    stderr: str,
    code: str,
) -> None:
    """Validate journal entry records command results."""
    expectation = JournalEntryExpectation(
        cmd=cmd, stdout=stdout, stderr=stderr, exit_code=int(code)
    )
    verify_journal_entry_details(mox, expectation)


@when(parsers.cfparse('I set environment variable "{var}" to "{val}"'))
def set_env_var(monkeypatch: pytest.MonkeyPatch, var: str, val: str) -> None:
    """Adjust environment variable to new value (scoped to the test)."""
    monkeypatch.setenv(var, val)


@scenario(
    str(FEATURES_DIR / "controller.feature"), "journal captures invocation details"
)
def test_journal_captures_invocation_details() -> None:
    """Journal records full invocation details."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "journal prunes excess entries")
def test_journal_prunes_excess_entries() -> None:
    """Journal drops older entries beyond configured size."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "invalid max journal size is rejected"
)
def test_invalid_max_journal_size_is_rejected() -> None:
    """Controller rejects non-positive journal size."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "verification reports unexpected invocation details",
)
def test_verification_reports_unexpected_invocation_details() -> None:
    """Verification errors include details for unexpected invocations."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "verification redacts sensitive environment values",
)
def test_verification_redacts_sensitive_environment_values() -> None:
    """Verification errors should redact sensitive environment variables."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"),
    "verification reports missing invocations",
)
def test_verification_reports_missing_invocations() -> None:
    """Verification errors highlight unfulfilled expectations."""
    pass
