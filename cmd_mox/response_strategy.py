"""Execute chosen response strategies and build domain responses.

The strategy selection and environment validation policy lives in
:mod:`cmd_mox.response_policy`; its former imports remain available here.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

from .domain import Response
from .environment import temporary_env
from .response_policy import (
    ResponseStrategy,
    apply_expectation_env,
    select_response_strategy,
)

if typ.TYPE_CHECKING:
    from .domain import Invocation
    from .test_doubles import CommandDouble


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


__all__ = [
    "ResponseStrategy",
    "apply_expectation_env",
    "default_response",
    "execute_handler",
    "finalize_response_env",
    "select_response_strategy",
]
