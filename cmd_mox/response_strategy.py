"""Pure helpers for turning a command double into an IPC :class:`Response`.

These helpers hold no controller state, which keeps the invocation-handling
policy (which strategy applies, how expectation environments are merged)
separable from :class:`~cmd_mox.controller.CmdMox` lifecycle management.
"""

from __future__ import annotations

import dataclasses as dc
import enum
import typing as typ

from .environment import temporary_env
from .errors import UnexpectedCommandError
from .ipc import Response

if typ.TYPE_CHECKING:
    from .ipc import Invocation
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


def default_response(invocation: Invocation) -> Response:
    """Return a default response when no double is registered.

    Returns
    -------
    Response
        A response echoing the invoked command name on stdout.
    """
    return Response(stdout=invocation.command)


def execute_handler(
    double: CommandDouble,
    invocation: Invocation,
    overrides: dict[str, str],
) -> Response:
    """Execute the handler with the appropriate environment context.

    Returns
    -------
    Response
        The handler's response, or a copy of the double's static response when
        no handler is configured.
    """
    if double.handler is None:
        base = double.response
        return dc.replace(base, env=dict(base.env))
    if overrides:
        with temporary_env(overrides):
            return double.handler(invocation)
    return double.handler(invocation)


def finalize_response_env(resp: Response, overrides: dict[str, str]) -> None:
    """Ensure response environment includes all expectation overrides."""
    if not overrides:
        return
    # Ensure the shim observes the injected variables even when the handler
    # returns a cached Response instance, without clobbering handler-set
    # overrides.
    for key, value in overrides.items():
        resp.env.setdefault(key, value)


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
    expectation_env = double.expectation.env or {}
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


__all__ = [
    "ResponseStrategy",
    "apply_expectation_env",
    "default_response",
    "execute_handler",
    "finalize_response_env",
    "select_response_strategy",
]
