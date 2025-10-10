"""Unit tests for controller helper functions."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.controller import CmdMox

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from cmd_mox.ipc import Invocation

from tests.helpers.controller import (
    CommandExecution,
    JournalEntryExpectation,
    _execute_command_with_params,
    _find_matching_journal_entry,
    _verify_journal_entry_with_expectation,
)

pytestmark = pytest.mark.requires_unix_sockets


def test_execute_and_verify_helpers() -> None:
    """Ensure helper functions execute and verify invocations."""

    def handler(inv: Invocation) -> tuple[str, str, int]:
        assert inv.args == ["--flag"]
        assert inv.stdin == "stdin"
        assert inv.env.get("ENV_VAR") == "VALUE"
        return ("handled", "", 0)

    with CmdMox() as mox:
        mox.stub("foo").runs(handler)
        mox.replay()
        params = CommandExecution(
            cmd="foo",
            args="--flag",
            stdin="stdin",
            env_var="ENV_VAR",
            env_val="VALUE",
        )
        result = _execute_command_with_params(params)
    # Sanity-check handler result
    assert result.stdout.strip() == "handled"
    assert result.returncode == 0
    expectation = JournalEntryExpectation(
        cmd="foo",
        args="--flag",
        stdin="stdin",
        env_var="ENV_VAR",
        env_val="VALUE",
        stdout="handled",
        stderr="",
        exit_code=0,
    )
    _verify_journal_entry_with_expectation(mox, expectation)


def test_find_matching_journal_entry_returns_most_recent() -> None:
    """Prefer the most recent journal entry when multiple match."""

    def handler(inv: Invocation) -> tuple[str, str, int]:
        return (inv.stdin, "", 0)

    with CmdMox() as mox:
        mox.stub("foo").runs(handler)
        mox.replay()
        params = CommandExecution(
            cmd="foo",
            args="--flag",
            stdin="first",
            env_var="ENV",
            env_val="VAL",
        )
        _execute_command_with_params(params)
        params = CommandExecution(
            cmd="foo",
            args="--flag",
            stdin="second",
            env_var="ENV",
            env_val="VAL",
        )
        _execute_command_with_params(params)
        expectation = JournalEntryExpectation(cmd="foo", args="--flag")
        inv = _find_matching_journal_entry(mox, expectation)
    assert inv.stdin == "second"


def test_helpers_raise_on_mismatch() -> None:
    """Helpers raise when expectations are not met."""

    def handler(inv: Invocation) -> tuple[str, str, int]:
        return ("out", "", 0)

    with CmdMox() as mox:
        mox.stub("foo").runs(handler)
        mox.replay()
        params = CommandExecution(
            cmd="foo",
            args="",
            stdin="",
            env_var="ENV",
            env_val="VAL",
        )
        _execute_command_with_params(params)
        expectation = JournalEntryExpectation(cmd="foo", exit_code=1)
        with pytest.raises(AssertionError, match="exit_code"):
            _verify_journal_entry_with_expectation(mox, expectation)
    with pytest.raises(AssertionError, match="does not contain expected entry"):
        _find_matching_journal_entry(mox, JournalEntryExpectation(cmd="bar"))
