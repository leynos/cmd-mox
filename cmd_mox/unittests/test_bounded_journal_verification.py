"""Regression tests for verification with a bounded diagnostic journal.

``max_journal_entries`` bounds only the public ``journal``. Verification reads a
separate internal record of immutable snapshots, so evicting a diagnostic entry
or mutating the public journal must never hide an unexpected or out-of-order
call.
"""

from __future__ import annotations

import threading
import typing as typ

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.environment import EnvironmentManager
from cmd_mox.errors import (
    ExpectationConfigurationError,
    UnexpectedCommandError,
    UnfulfilledExpectationError,
)
from cmd_mox.ipc import Invocation, PassthroughResult, Response

if typ.TYPE_CHECKING:
    import collections.abc as cabc


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


def _assert_evicted_call_fails_verification(
    mox: CmdMox, fragments: tuple[str, ...]
) -> None:
    """Evict the final call, then require verification to report the first."""
    _dispatch(mox, "ok")
    assert [inv.command for inv in mox.journal] == ["ok"]
    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._run_verifiers()

    message = str(excinfo.value)
    for fragment in fragments:
        assert fragment in message, f"{fragment!r} missing from {message!r}"


def test_evicted_unregistered_command_is_still_reported() -> None:
    """An unregistered call evicted from the journal still fails verification."""
    mox = CmdMox(max_journal_entries=1)
    mox.register_command("rm")
    mox.stub("ok").returns()
    _dispatch(mox, "rm")

    _assert_evicted_call_fails_verification(
        mox, ("Unexpected command invocation.", "Actual call:")
    )


def test_evicted_mismatching_spy_call_is_still_reported() -> None:
    """A mismatching spy call evicted from the journal still fails verification."""
    mox = CmdMox(max_journal_entries=1)
    mox.spy("git").with_args("status")
    mox.stub("ok").returns()
    _dispatch(mox, "git", "commit")

    _assert_evicted_call_fails_verification(mox, ("Expected:", "Actual:", "Reason:"))


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


def test_reversing_public_journal_does_not_change_verification() -> None:
    """Verification ignores the mutable public journal's ordering."""
    mox = CmdMox()
    mox.mock("a").returns().in_order()
    mox.mock("b").returns().in_order()
    _dispatch(mox, "b")
    _dispatch(mox, "a")
    assert [inv.command for inv in mox.journal] == ["b", "a"]

    mox.journal.reverse()

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._run_verifiers()
    assert "Ordered expectation violated." in str(excinfo.value)


def test_mutating_journal_entry_does_not_change_verification() -> None:
    """Verification keeps its own copy, so journal edits cannot hide a call."""
    mox = CmdMox()
    mox.register_command("rm")
    mox.stub("ok").returns()
    _dispatch(mox, "rm")

    assert mox._verification_log[0] is not mox.journal[0]
    mox.journal[0].command = "ok"

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._run_verifiers()
    assert "rm()" in str(excinfo.value)


def test_mutating_journal_entry_arguments_does_not_change_verification() -> None:
    """Verification snapshots argument lists, not just the entry object."""
    mox = CmdMox()
    mox.mock("git").with_args("status").returns().in_order()
    _dispatch(mox, "git", "commit")

    mox.journal[0].args[:] = ["status"]

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._run_verifiers()
    assert "Expected:" in str(excinfo.value)


class _ReleaseHookLock:
    """Lock that runs a one-shot callback after its first release."""

    def __init__(self, on_first_release: cabc.Callable[[], None]) -> None:
        """Store the callback to run once the first holder releases."""
        self._lock = threading.Lock()
        self._on_first_release = on_first_release
        self._fired = False

    def __enter__(self) -> _ReleaseHookLock:
        """Acquire the underlying lock."""
        self._lock.acquire()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        """Release the lock, running the callback after the first release."""
        self._lock.release()
        if not self._fired:
            self._fired = True
            self._on_first_release()


def test_in_order_registration_is_atomic_with_call_recording(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call completing inside the ordering guard must still be retained."""
    mox = CmdMox(max_journal_entries=1)
    double = mox.stub("race").returns()

    # The hook fires when the guard releases its lock. The old two-step
    # registration released before appending to the ordered list, so the
    # hooked call was discarded and in_order() succeeded against no history.
    hook = _ReleaseHookLock(lambda: _dispatch(mox, "race"))
    monkeypatch.setattr(mox, "_history_lock", hook)

    double.in_order()

    assert [inv.command for inv in mox._verification_log] == ["race"]
    mox._run_verifiers()


def test_rejected_late_in_order_leaves_expectation_unordered() -> None:
    """A rejected in_order() must not mark the expectation as ordered."""
    mox = CmdMox(max_journal_entries=1)
    double = mox.stub("first").returns()
    _dispatch(mox, "first")

    with pytest.raises(ExpectationConfigurationError):
        double.in_order()

    assert double.expectation.ordered is False
    assert double.expectation not in mox._ordered


def test_in_order_with_in_flight_stub_call_retains_the_call() -> None:
    """A call in flight when ordering is applied is retained as ordered."""
    mox = CmdMox(max_journal_entries=1)
    started = threading.Event()
    release = threading.Event()

    def handler(_invocation: Invocation) -> Response:
        """Hold the call in flight until ordering has been applied."""
        started.set()
        release.wait(timeout=5)
        return Response()

    double = mox.stub("race").runs(handler)
    worker = threading.Thread(target=_dispatch, args=(mox, "race"))
    worker.start()
    try:
        assert started.wait(timeout=5)
        double.in_order()
    finally:
        release.set()
        worker.join(timeout=5)

    assert not worker.is_alive()
    assert [inv.command for inv in mox._verification_log] == ["race"]
    mox._run_verifiers()


def test_count_diagnostics_ignore_journal_entry_mutation() -> None:
    """Count failures describe the recorded call, not a mutated journal entry."""
    mox = CmdMox()
    mox.mock("git").with_args("status").times(2)
    _dispatch(mox, "git", "status")

    mox.journal[0].args[:] = ["mutated"]

    with pytest.raises(UnfulfilledExpectationError) as excinfo:
        mox._run_verifiers()
    message = str(excinfo.value)
    assert "git('status')" in message
    assert "mutated" not in message


@pytest.mark.parametrize("max_journal_entries", [1, None])
def test_late_mock_registration_does_not_claim_earlier_call(
    max_journal_entries: int | None,
) -> None:
    """A call made without a double is unexpected even if a mock follows."""
    mox = CmdMox(max_journal_entries=max_journal_entries)
    mox.register_command("git")
    _dispatch(mox, "git", "status")
    mox.mock("git").with_args("status").returns()

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._run_verifiers()
    assert "Unexpected command invocation." in str(excinfo.value)


def test_late_stub_registration_does_not_claim_earlier_call() -> None:
    """A call made before its stub was registered is still unexpected."""
    mox = CmdMox()
    mox.register_command("deploy")
    _dispatch(mox, "deploy")
    mox.stub("deploy").returns()

    with pytest.raises(UnexpectedCommandError):
        mox._run_verifiers()


def test_call_dispatched_to_mock_counts_for_its_expectation() -> None:
    """Dispatch-time association still counts calls made after registration."""
    mox = CmdMox()
    mox.mock("git").with_args("status").returns()
    _dispatch(mox, "git", "status")

    mox._run_verifiers()


def test_late_in_order_is_rejected_when_bounded_journal_discards_calls() -> None:
    """A bounded journal rejects in_order() after a stub call was discarded."""
    mox = CmdMox(max_journal_entries=1)
    first = mox.stub("first").returns()
    second = mox.stub("second").returns()
    _dispatch(mox, "first")
    _dispatch(mox, "second")

    with pytest.raises(ExpectationConfigurationError, match="max_journal_entries"):
        first.in_order()
    with pytest.raises(ExpectationConfigurationError, match="second"):
        second.in_order()

    # A stub that has not run can still be marked ordered.
    mox.stub("third").returns().in_order()


@pytest.mark.parametrize(("kind", "max_journal_entries"), [("mock", 1), ("stub", None)])
def test_late_in_order_accepts_completed_calls(
    kind: str, max_journal_entries: int | None
) -> None:
    """Retained calls satisfy in_order() applied after they complete."""
    mox = CmdMox(max_journal_entries=max_journal_entries)
    first = getattr(mox, kind)("first").returns()
    second = getattr(mox, kind)("second").returns()
    _dispatch(mox, "first")
    _dispatch(mox, "second")

    first.in_order()
    second.in_order()

    mox._run_verifiers()


@pytest.mark.parametrize(("kind", "max_journal_entries"), [("mock", 1), ("stub", None)])
def test_late_in_order_reports_reversed_calls(
    kind: str, max_journal_entries: int | None
) -> None:
    """Late ordering still reports calls that completed out of order."""
    mox = CmdMox(max_journal_entries=max_journal_entries)
    first = getattr(mox, kind)("first").returns()
    second = getattr(mox, kind)("second").returns()
    _dispatch(mox, "second")
    _dispatch(mox, "first")

    first.in_order()
    second.in_order()

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._run_verifiers()
    assert "Ordered expectation violated." in str(excinfo.value)


def test_verification_log_retains_only_calls_verification_uses() -> None:
    """A bounded record keeps only the calls verification needs."""
    mox = CmdMox(max_journal_entries=1)
    mox.stub("plain").returns()
    mox.stub("ordered").returns().in_order()

    _dispatch(mox, "plain")
    _dispatch(mox, "ordered")

    assert [inv.command for inv in mox._verification_log] == ["ordered"]


def test_unbounded_journal_retains_every_call_for_late_ordering() -> None:
    """An unbounded record keeps unordered stub calls for late in_order()."""
    mox = CmdMox()
    mox.stub("plain").returns()

    _dispatch(mox, "plain")

    assert [inv.command for inv in mox._verification_log] == ["plain"]


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
