"""Regression tests for verification with a bounded diagnostic journal.

``max_journal_entries`` bounds only the public ``journal``. Verification reads a
separate internal record, so evicting a diagnostic entry must never hide an
unexpected or out-of-order call.
"""

from __future__ import annotations

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.environment import EnvironmentManager
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.ipc import Invocation, PassthroughResult


def _invocation(command: str, *args: str) -> Invocation:
    """Build an invocation for direct dispatch."""
    return Invocation(command=command, args=list(args), stdin="", env={})


def _dispatch(mox: CmdMox, command: str, *args: str) -> None:
    """Dispatch a completed invocation through the controller."""
    mox._handle_invocation(_invocation(command, *args))


def _complete_passthrough(mox: CmdMox, invocation_id: str, *, stdout: str = "") -> None:
    """Deliver a passthrough result for a prepared invocation."""
    mox._handle_passthrough_result(
        PassthroughResult(
            invocation_id=invocation_id,
            stdout=stdout,
            stderr="",
            exit_code=0,
        )
    )


def test_evicted_unregistered_command_is_still_reported() -> None:
    """An unregistered call evicted from the journal still fails verification."""
    mox = CmdMox(max_journal_entries=1)
    mox.register_command("rm")
    mox.stub("ok").returns()

    _dispatch(mox, "rm")
    _dispatch(mox, "ok")

    assert [inv.command for inv in mox.journal] == ["ok"]
    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._run_verifiers()

    message = str(excinfo.value)
    assert "Unexpected command invocation." in message
    assert "Actual call:" in message


def test_evicted_mismatching_spy_call_is_still_reported() -> None:
    """A mismatching spy call evicted from the journal still fails verification."""
    mox = CmdMox(max_journal_entries=1)
    mox.spy("git").with_args("status")
    mox.stub("ok").returns()

    _dispatch(mox, "git", "commit")
    _dispatch(mox, "ok")

    assert [inv.command for inv in mox.journal] == ["ok"]
    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._run_verifiers()

    message = str(excinfo.value)
    for fragment in ("Expected:", "Actual:", "Reason:"):
        assert fragment in message, f"{fragment!r} missing from {message!r}"


@pytest.mark.parametrize("max_journal_entries", [1, None])
def test_ordered_mocks_pass_with_bounded_journal(
    max_journal_entries: int | None,
) -> None:
    """Ordered mocks verify correctly even when the journal keeps only one call."""
    mox = CmdMox(max_journal_entries=max_journal_entries)
    mox.mock("a").returns().in_order()
    mox.mock("b").returns().in_order()

    _dispatch(mox, "a")
    _dispatch(mox, "b")

    if max_journal_entries == 1:
        assert [inv.command for inv in mox.journal] == ["b"]
    mox._run_verifiers()


@pytest.mark.parametrize("max_journal_entries", [1, None])
def test_ordered_mocks_in_reverse_are_reported(
    max_journal_entries: int | None,
) -> None:
    """Reversed ordered mocks fail even when the journal keeps only one call."""
    mox = CmdMox(max_journal_entries=max_journal_entries)
    mox.mock("a").returns().in_order()
    mox.mock("b").returns().in_order()

    _dispatch(mox, "b")
    _dispatch(mox, "a")

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._run_verifiers()

    message = str(excinfo.value)
    assert "Ordered expectation violated." in message
    assert "First mismatch:" in message


def test_passthrough_spy_is_recorded_only_on_completion() -> None:
    """A passthrough call enters both records only when its result arrives."""
    mox = CmdMox(max_journal_entries=1)
    mox.spy("echo").with_args("expected").passthrough()

    response = mox._handle_invocation(_invocation("echo", "actual"))
    directive = response.passthrough
    assert directive is not None
    assert len(mox.journal) == 0
    assert mox._verification_log == []

    _complete_passthrough(mox, directive.invocation_id)
    assert len(mox.journal) == 1
    assert len(mox._verification_log) == 1

    mox.stub("ok").returns()
    _dispatch(mox, "ok")
    assert [inv.command for inv in mox.journal] == ["ok"]

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._run_verifiers()
    assert "Expected:" in str(excinfo.value)


def test_passthrough_results_follow_completion_order() -> None:
    """Both records order passthrough calls by completion, not preparation."""
    mox = CmdMox(max_journal_entries=2)
    mox.spy("first").passthrough()
    mox.spy("second").passthrough()

    first = mox._handle_invocation(_invocation("first")).passthrough
    second = mox._handle_invocation(_invocation("second")).passthrough
    assert first is not None
    assert second is not None

    _complete_passthrough(mox, second.invocation_id)
    _complete_passthrough(mox, first.invocation_id)

    assert [inv.command for inv in mox.journal] == ["second", "first"]
    assert [inv.command for inv in mox._verification_log] == ["second", "first"]


def test_verification_log_retains_only_calls_verification_uses() -> None:
    """Unordered stub calls stay out of the record; ordered ones stay in."""
    mox = CmdMox()
    mox.stub("plain").returns()
    mox.stub("ordered").returns().in_order()

    _dispatch(mox, "plain")
    _dispatch(mox, "ordered")

    assert [inv.command for inv in mox._verification_log] == ["ordered"]


def test_idempotent_replay_keeps_verification_log_and_verify_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A no-op replay must not clear the record, and verify still tears down."""
    mox = CmdMox(verify_on_exit=False)
    mox.register_command("rm")
    mox.__enter__()
    monkeypatch.setattr(mox, "_start_ipc_server", lambda: None)
    mox.replay()

    _dispatch(mox, "rm")
    assert [inv.command for inv in mox._verification_log] == ["rm"]

    mox.replay()
    assert [inv.command for inv in mox._verification_log] == ["rm"]

    with pytest.raises(UnexpectedCommandError):
        mox.verify()

    assert not mox._entered
    assert EnvironmentManager.get_active_manager() is None
