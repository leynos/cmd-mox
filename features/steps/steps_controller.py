"""Step definitions for CmdMox behavioural tests."""
# pyright: reportMissingImports=false, reportUnknownMemberType=false

from __future__ import annotations

import subprocess
import typing as t

from behave import given, then, when  # type: ignore[attr-defined]

from cmd_mox.controller import CmdMox


class BehaveContext(t.Protocol):
    """Behave step context with attributes used in tests."""

    mox: CmdMox
    result: subprocess.CompletedProcess[str]


@given("a CmdMox controller")
def step_create_controller(context: BehaveContext) -> None:
    """Create a :class:`CmdMox` instance for the scenario."""
    context.mox = CmdMox()


@given('the command "{cmd}" is stubbed to return "{text}"')
def step_stub_command(context: BehaveContext, cmd: str, text: str) -> None:
    """Configure a stubbed command that returns *text*."""
    context.mox.stub(cmd).returns(stdout=text)


@given('the command "{cmd}" is mocked to return "{text}"')
def step_mock_command(context: BehaveContext, cmd: str, text: str) -> None:
    """Configure a mocked command that returns *text*."""
    context.mox.mock(cmd).returns(stdout=text)


@given('the command "{cmd}" is spied to return "{text}"')
def step_spy_command(context: BehaveContext, cmd: str, text: str) -> None:
    """Configure a spied command that returns *text*."""
    context.mox.spy(cmd).returns(stdout=text)


@when("I replay the controller")
def step_replay(context: BehaveContext) -> None:
    """Invoke :meth:`CmdMox.replay`."""
    context.mox.__enter__()
    context.mox.replay()


@when('I run the command "{cmd}"')
def step_run_command(context: BehaveContext, cmd: str) -> None:
    """Run the stubbed command."""
    context.result = subprocess.run(  # noqa: S603
        [cmd], capture_output=True, text=True, check=True
    )


@then('the output should be "{text}"')
def step_check_output(context: BehaveContext, text: str) -> None:
    """Verify the captured output."""
    assert context.result.stdout.strip() == text  # noqa: S101


@then('the journal should contain {count:d} invocation of "{cmd}"')
def step_check_journal(context: BehaveContext, count: int, cmd: str) -> None:
    """Check that *cmd* was recorded *count* times."""
    context.mox.verify()
    assert len(context.mox.journal) == count  # noqa: S101
    assert context.mox.journal[0].command == cmd  # noqa: S101


@then('the spy "{cmd}" should record {count:d} invocation')
def step_check_spy(context: BehaveContext, cmd: str, count: int) -> None:
    """Ensure the named spy recorded *count* calls."""
    context.mox.verify()
    spy = context.mox.spies[cmd]
    assert len(spy.invocations) == count  # noqa: S101
