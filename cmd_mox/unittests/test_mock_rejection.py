"""Unit tests for the fail-fast rejection ledger and its response."""

from __future__ import annotations

from cmd_mox._mock_rejection import RejectionLedger, rejection_response
from cmd_mox.controller import CmdMox
from cmd_mox.domain import Invocation


def _invocation(*args: str, stdin: str = "") -> Invocation:
    """Return a ``git`` invocation carrying *args* and *stdin*."""
    return Invocation("git", list(args), stdin, {})


def test_ledger_reports_the_earliest_rejection() -> None:
    """Verification must report the first rejected call of a replay."""
    double = CmdMox().mock("git")
    ledger = RejectionLedger()
    first = _invocation("push")
    second = _invocation("pull")

    ledger.record(double, first, "first reason")
    ledger.record(double, second, "second reason")

    earliest = ledger.first()
    assert earliest is not None
    assert earliest.invocation is first
    assert earliest.reason == "first reason"


def test_ledger_is_empty_before_recording_and_after_clearing() -> None:
    """A new replay starts without the rejections of the previous one."""
    double = CmdMox().mock("git")
    ledger = RejectionLedger()

    assert ledger.first() is None

    ledger.record(double, _invocation("push"), "reason")
    ledger.clear()

    assert ledger.first() is None


def test_rejection_response_reports_failure_with_redacted_arguments() -> None:
    """The shim response masks a credential under a sensitive option."""
    response = rejection_response(
        _invocation("--token", "supersecret"), "arguments do not match"
    )

    assert response.exit_code == 1
    assert response.stdout == ""
    assert "supersecret" not in response.stderr
    assert "git --token <redacted>" in response.stderr
    assert "(arguments do not match)" in response.stderr
