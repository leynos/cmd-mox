"""Unit tests for :meth:`cmd_mox.test_doubles.CommandDouble.matches`."""

from __future__ import annotations

from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

from cmd_mox.controller import CmdMox
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation


def test_matches_delegates_only_for_its_own_command() -> None:
    """matches() accepts its command and rejects others before delegation."""
    double = CmdMox().stub("git").with_args("status")
    matching_invocation = Invocation("git", ["status"], "", {})
    mismatched_invocation = Invocation("hg", ["status"], "", {})

    with mock.patch.object(
        Expectation, "matches", autospec=True, return_value=True
    ) as matches:
        assert double.matches(matching_invocation), (
            "matches() should accept an invocation for its own command"
        )
        matches.assert_called_once_with(double.expectation, matching_invocation)

        matches.reset_mock()

        assert not double.matches(mismatched_invocation), (
            "matches() should reject a different command without delegating"
        )
        matches.assert_not_called()


@given(command_pair=st.tuples(st.text(), st.text()))
def test_matches_rejects_every_different_command_without_delegating(
    command_pair: tuple[str, str],
) -> None:
    """matches() short-circuits arbitrary command-name mismatches."""
    command, different_command = command_pair
    if command == different_command:
        different_command = f"{different_command}\x00"

    double = CmdMox().stub(command)
    invocation = Invocation(different_command, [], "", {})

    with mock.patch.object(
        Expectation, "matches", autospec=True, return_value=True
    ) as matches:
        assert not double.matches(invocation), (
            "matches() should short-circuit a command mismatch without delegating"
        )
        matches.assert_not_called()
