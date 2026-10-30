"""Direct tests for response strategy and expectation environment policy."""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox import response_strategy
from cmd_mox.controller import CmdMox
from cmd_mox.domain import Invocation
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.response_policy import (
    ResponseStrategy,
    apply_expectation_env,
    select_response_strategy,
)

if typ.TYPE_CHECKING:
    from cmd_mox.test_doubles import CommandDouble


def test_execution_adapter_reexports_policy_names() -> None:
    """Existing adapter imports retain the policy objects' identities."""
    assert response_strategy.ResponseStrategy is ResponseStrategy, "Strategy moved"
    assert response_strategy.select_response_strategy is select_response_strategy, (
        "Strategy selector moved"
    )
    assert response_strategy.apply_expectation_env is apply_expectation_env, (
        "Environment policy moved"
    )


@pytest.fixture
def configured_double() -> CommandDouble:
    """Build a double with overlapping and new expectation values.

    Returns
    -------
    CommandDouble
        A stub whose expected environment has two entries.
    """
    return CmdMox().stub("git").with_env({"EXISTING": "same", "ADDED": "new"})


def test_select_missing_double() -> None:
    """An absent double selects the missing-double strategy."""
    assert select_response_strategy(None) is ResponseStrategy.MISSING_DOUBLE, (
        "An absent double selected the wrong strategy"
    )


def test_select_passthrough_double() -> None:
    """A passthrough spy selects the passthrough strategy."""
    double = CmdMox().spy("git").passthrough()

    assert select_response_strategy(double) is ResponseStrategy.PASSTHROUGH, (
        "A passthrough spy selected the wrong strategy"
    )


def test_select_regular_double(configured_double: CommandDouble) -> None:
    """An ordinary double selects the regular strategy."""
    assert select_response_strategy(configured_double) is ResponseStrategy.REGULAR, (
        "An ordinary double selected the wrong strategy"
    )


def test_expectation_environment_merges_and_returns_a_copy(
    configured_double: CommandDouble,
) -> None:
    """Matching values merge without discarding unrelated invocation values."""
    invocation = Invocation(
        command="git", args=[], stdin="", env={"EXISTING": "same", "OTHER": "kept"}
    )

    overrides = apply_expectation_env(configured_double, invocation)

    expected_env = {"EXISTING": "same", "OTHER": "kept", "ADDED": "new"}
    assert invocation.env == expected_env, "Invocation environment was not merged"
    assert overrides == {"EXISTING": "same", "ADDED": "new"}, (
        "Returned overrides did not match the expectation"
    )
    overrides["ADDED"] = "changed"
    assert configured_double.expectation.env["ADDED"] == "new", (
        "Returned overrides shared the expectation mapping"
    )


def test_expectation_environment_conflict_preserves_invocation(
    configured_double: CommandDouble,
) -> None:
    """A conflict reports the existing value before applying any overrides."""
    invocation = Invocation(command="git", args=[], stdin="", env={"EXISTING": "other"})

    with pytest.raises(UnexpectedCommandError) as error:
        apply_expectation_env(configured_double, invocation)

    assert str(error.value) == (
        "Invocation for 'git' provided conflicting environment values: EXISTING='other'"
    ), "Conflict message changed"
    assert invocation.env == {"EXISTING": "other"}, "Conflict mutated the invocation"


def test_empty_expectation_environment_leaves_invocation_unchanged() -> None:
    """An expectation without overrides leaves the invocation environment alone."""
    double = CmdMox().stub("git")
    invocation = Invocation(command="git", args=[], stdin="", env={"OTHER": "kept"})

    assert apply_expectation_env(double, invocation) == {}, "Unexpected overrides"
    assert invocation.env == {"OTHER": "kept"}, "Invocation environment changed"
