"""Unit tests for controller helper functions."""

from __future__ import annotations

import typing as t

from cmd_mox.controller import CmdMox

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from cmd_mox.ipc import Invocation

from test_controller_bdd import (
    CommandExecution,
    ExpectedInvocation,
    execute_command_with_details,
    verify_journal_entry_details,
)


def test_execute_and_verify_helpers() -> None:
    """Ensure helper functions execute and verify invocations."""
    mox = CmdMox()

    def handler(inv: Invocation) -> tuple[str, str, int]:
        assert inv.args == ["--flag"]
        assert inv.stdin == "stdin"
        assert inv.env.get("ENV_VAR") == "VALUE"
        return ("handled", "", 0)

    mox.stub("foo").runs(handler)

    with mox:
        mox.replay()
        params = CommandExecution(
            cmd="foo",
            args="--flag",
            stdin="stdin",
            env_var="ENV_VAR",
            env_val="VALUE",
        )
        execute_command_with_details(mox, params)

    expectation = ExpectedInvocation(
        args="--flag",
        stdin="stdin",
        env_var="ENV_VAR",
        env_val="VALUE",
    )
    verify_journal_entry_details(mox, "foo", expectation)
