"""Tests for the ordered expectation verifier."""

from __future__ import annotations

import pytest

from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation
from cmd_mox.verifiers import OrderVerifier


def _invocation(command: str, *args: str) -> Invocation:
    """Create a minimal invocation for *command* with *args*."""
    return Invocation(command=command, args=list(args), stdin="", env={})


def test_order_verifier_ignores_unordered_invocations_with_same_command() -> None:
    """Unordered expectations sharing a command name should not trip ordering."""
    fetch = Expectation("git").with_args("fetch").in_order()

    journal = [
        _invocation("git", "status"),
        _invocation("git", "fetch"),
    ]

    OrderVerifier([fetch]).verify(journal)


def test_order_verifier_flags_out_of_order_matches() -> None:
    """An invocation matching a later ordered expectation should fail fast."""
    first = Expectation("git").with_args("fetch").in_order()
    second = Expectation("git").with_args("status").in_order()

    journal = [
        _invocation("git", "status"),
        _invocation("git", "fetch"),
    ]

    with pytest.raises(UnexpectedCommandError):
        OrderVerifier([first, second]).verify(journal)
