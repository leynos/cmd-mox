"""Domain rules for selecting responses and validating expectation environments."""

from __future__ import annotations

import enum
import typing as typ

from .errors import UnexpectedCommandError

if typ.TYPE_CHECKING:
    from .domain import Invocation
    from .expectations import Expectation
    from .test_doubles import CommandDouble


class ResponseStrategy(enum.Enum):
    """Response selection strategy for invocation handling."""

    MISSING_DOUBLE = enum.auto()
    PASSTHROUGH = enum.auto()
    REGULAR = enum.auto()


def select_response_strategy(double: CommandDouble | None) -> ResponseStrategy:
    """Determine the response strategy for the given double.

    Returns
    -------
    ResponseStrategy
        The strategy matching the double's registration and mode.
    """
    if double is None:
        return ResponseStrategy.MISSING_DOUBLE
    if double.passthrough_mode:
        return ResponseStrategy.PASSTHROUGH
    return ResponseStrategy.REGULAR


def apply_expectation_env(
    double: CommandDouble, invocation: Invocation
) -> dict[str, str]:
    """Validate and apply expectation environment to invocation.

    Returns
    -------
    dict[str, str]
        The environment overrides that were applied.

    Raises
    ------
    UnexpectedCommandError
        When the expectation environment conflicts with invocation environment.
    """
    expectation: Expectation = double.expectation
    expectation_env = expectation.env or {}
    overrides = dict(expectation_env)

    if not overrides:
        return overrides

    conflicts = {
        key: invocation.env[key]
        for key, value in overrides.items()
        if key in invocation.env and invocation.env[key] != value
    }

    if conflicts:
        conflict_list = ", ".join(f"{k}={v!r}" for k, v in conflicts.items())
        msg = (
            f"Invocation for {invocation.command!r} provided conflicting "
            f"environment values: {conflict_list}"
        )
        raise UnexpectedCommandError(msg)

    invocation.env.update(overrides)
    return overrides


__all__ = ["ResponseStrategy", "apply_expectation_env", "select_response_strategy"]
