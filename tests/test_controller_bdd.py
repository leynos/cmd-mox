"""Behavioural tests for CmdMox controller using pytest-bdd."""

from __future__ import annotations

import contextlib
import os
import subprocess
import typing as t
from pathlib import Path

from pytest_bdd import given, parsers, scenario, then, when

from cmd_mox.controller import CmdMox

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from cmd_mox.ipc import Invocation

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"


@given("a CmdMox controller", target_fixture="mox")
def create_controller() -> CmdMox:
    """Create a fresh CmdMox instance."""
    return CmdMox()


@given(parsers.cfparse('the command "{cmd}" is stubbed to return "{text}"'))
def stub_command(mox: CmdMox, cmd: str, text: str) -> None:
    """Configure a stubbed command."""
    mox.stub(cmd).returns(stdout=text)


@given(parsers.cfparse('the command "{cmd}" is mocked to return "{text}"'))
def mock_command(mox: CmdMox, cmd: str, text: str) -> None:
    """Configure a mocked command."""
    mox.mock(cmd).returns(stdout=text)


@given(
    parsers.cfparse('the command "{cmd}" is mocked to return "{text}" times {count:d}')
)
def mock_command_times(mox: CmdMox, cmd: str, text: str, count: int) -> None:
    """Configure a mocked command with an expected call count."""
    mox.mock(cmd).returns(stdout=text).times(count)


@given(parsers.cfparse('the command "{cmd}" is spied to return "{text}"'))
def spy_command(mox: CmdMox, cmd: str, text: str) -> None:
    """Configure a spied command."""
    mox.spy(cmd).returns(stdout=text)


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
    mox.mock(cmd).with_args(*args.split()).returns(stdout=text).in_order()


@given(parsers.cfparse('the command "{cmd}" is stubbed with env var "{var}"="{val}"'))
def stub_with_env(mox: CmdMox, cmd: str, var: str, val: str) -> None:
    """Stub command that outputs an injected env variable."""

    def handler(invocation: Invocation) -> tuple[str, str, int]:
        return (os.environ.get(var, ""), "", 0)

    mox.stub(cmd).with_env({var: val}).runs(handler)


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
    parsers.cfparse('I run the command "{cmd}" with arguments "{args}"'),
    target_fixture="result",
)
def run_command_args(
    mox: CmdMox, cmd: str, args: str
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* with additional arguments."""
    argv = [cmd, *args.split()]
    return subprocess.run(argv, capture_output=True, text=True, check=True, shell=False)  # noqa: S603


@when("I verify the controller")
def verify_controller(mox: CmdMox, mox_stack: contextlib.ExitStack) -> None:
    """Invoke verification and close context."""
    mox.verify()
    mox_stack.close()


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


@then(parsers.cfparse('the journal should contain {count:d} invocation of "{cmd}"'))
def check_journal(mox: CmdMox, count: int, cmd: str) -> None:
    """Verify the journal contains the expected command invocation."""
    assert len(mox.journal) == count
    if count > 0:
        assert mox.journal[0].command == cmd


@then(parsers.cfparse('the spy "{cmd}" should record {count:d} invocation'))
def check_spy(mox: CmdMox, cmd: str, count: int) -> None:
    """Verify the spy recorded the invocation."""
    assert cmd in mox.spies, f"Spy for command '{cmd}' not found"
    spy = mox.spies[cmd]
    assert len(spy.invocations) == count


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


@scenario(str(FEATURES_DIR / "controller.feature"), "mocked command execution")
def test_mocked_command_execution() -> None:
    """Mocked command returns expected output."""
    pass


@scenario(str(FEATURES_DIR / "controller.feature"), "spy records invocation")
def test_spy_records_invocation() -> None:
    """Spy records command invocation."""
    pass


@scenario(
    str(FEATURES_DIR / "controller.feature"), "journal preserves invocation order"
)
def test_journal_preserves_order() -> None:
    """Journal records commands in order."""
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
