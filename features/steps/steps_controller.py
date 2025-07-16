"""Step definitions for CmdMox behavioural tests."""
# pyright: reportMissingImports=false, reportUnknownMemberType=false

from __future__ import annotations

import subprocess

from behave import given, then, when  # type: ignore[attr-defined]
import typing as t

from cmd_mox.controller import CmdMox


@given("a CmdMox controller")
def step_create_controller(context: t.Any) -> None:
    """Create a :class:`CmdMox` instance for the scenario."""
    context.mox = CmdMox()


@given('the command "{cmd}" is stubbed to return "{text}"')
def step_stub_command(context: t.Any, cmd: str, text: str) -> None:
    """Configure a stubbed command that returns *text*."""
    context.mox.stub(cmd).returns(stdout=text)


@when("I replay the controller")
def step_replay(context: t.Any) -> None:
    """Invoke :meth:`CmdMox.replay`."""
    context.mox.replay()


@when('I run the command "{cmd}"')
def step_run_command(context: t.Any, cmd: str) -> None:
    """Run the stubbed command."""
    context.result = subprocess.run(  # noqa: S603
        [cmd], capture_output=True, text=True, check=True
    )


@then('the output should be "{text}"')
def step_check_output(context: t.Any, text: str) -> None:
    """Verify the captured output."""
    assert context.result.stdout.strip() == text  # noqa: S101


@then('the journal should contain {count:d} invocation of "{cmd}"')
def step_check_journal(context: t.Any, count: int, cmd: str) -> None:
    """Check that *cmd* was recorded *count* times."""
    context.mox.verify()
    assert len(context.mox.journal) == count  # noqa: S101
    assert context.mox.journal[0].command == cmd  # noqa: S101
