# ruff: noqa: S101
"""pytest-bdd steps that validate journal behaviour."""

from __future__ import annotations

import typing as t

from pytest_bdd import parsers, then

from tests.helpers.controller import (
    JournalEntryExpectation,
    verify_journal_entry_details,
)
from tests.steps.command_execution import _resolve_empty_placeholder

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from cmd_mox.controller import CmdMox


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


@then(parsers.cfparse("the journal order should be {commands}"))
def check_journal_order(mox: CmdMox, commands: str) -> None:
    """Ensure the journal entries are in the expected order."""
    expected = commands.split(",")
    actual = [inv.command for inv in mox.journal]
    assert actual == expected


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
    resolved_args = _resolve_empty_placeholder(args)
    resolved_stdin = _resolve_empty_placeholder(stdin)
    expectation = JournalEntryExpectation(
        cmd,
        resolved_args,
        resolved_stdin,
        var,
        val,
    )
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
