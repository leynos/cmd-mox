"""Behavioural tests for CmdMox controller using pytest-bdd."""

from __future__ import annotations

import contextlib
import os
import subprocess
from pathlib import Path

from pytest_bdd import given, parsers, scenario, then, when

from cmd_mox.controller import CmdMox

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


@given(parsers.cfparse('the command "{cmd}" is spied to return "{text}"'))
def spy_command(mox: CmdMox, cmd: str, text: str) -> None:
    """Configure a spied command."""
    mox.spy(cmd).returns(stdout=text)


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
        [cmd], capture_output=True, text=True, check=True
    )


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
        result = subprocess.run([cmd], capture_output=True, text=True, check=True)  # noqa: S603
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
