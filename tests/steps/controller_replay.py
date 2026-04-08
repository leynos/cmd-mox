"""pytest-bdd steps for controller replay integration scenarios."""

from __future__ import annotations

import shlex
import typing as t

import pytest
from pytest_bdd import given, parsers, then, when

from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.ipc import Invocation
from tests.helpers.fixtures import write_minimal_replay_fixture

if t.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox.controller import CmdMox


@given("a git replay fixture exists", target_fixture="git_replay_fixture_path")
def git_replay_fixture(tmp_path: Path) -> Path:
    """Create a replay fixture containing ``git status`` -> ``ok``."""
    return write_minimal_replay_fixture(tmp_path)


@given(parsers.cfparse('the command "{cmd}" is spied with that replay fixture'))
def spy_with_replay_fixture(
    mox: CmdMox, cmd: str, git_replay_fixture_path: Path
) -> None:
    """Attach a strict replay fixture to a spy."""
    mox.spy(cmd).replay(git_replay_fixture_path)


@given(
    parsers.cfparse(
        'the command "{cmd}" is spied with fuzzy replay and fallback "{text}"'
    )
)
def spy_with_fuzzy_replay_and_fallback(
    mox: CmdMox, cmd: str, text: str, git_replay_fixture_path: Path
) -> None:
    """Attach a fuzzy replay fixture and canned fallback response to a spy."""
    mox.spy(cmd).returns(stdout=text).replay(git_replay_fixture_path, strict=False)


@when(
    parsers.cfparse(
        'the controller handles the invocation for "{cmd}" with arguments "{args}" '
        "expecting UnexpectedCommandError"
    ),
    target_fixture="invocation_error",
)
def handle_invocation_expect_error(
    mox: CmdMox, cmd: str, args: str
) -> UnexpectedCommandError:
    """Call ``_handle_invocation()`` directly and capture the strict replay error."""
    invocation = Invocation(command=cmd, args=shlex.split(args), stdin="", env={})

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._handle_invocation(invocation)

    return excinfo.value


@then(parsers.cfparse('the invocation error message should contain "{text}"'))
def invocation_error_contains(
    invocation_error: UnexpectedCommandError, text: str
) -> None:
    """Assert the captured invocation error contains *text*."""
    assert text in str(invocation_error)
