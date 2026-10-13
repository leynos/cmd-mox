"""Shared JSON parsing helpers for IPC messaging."""

from __future__ import annotations

import json
import logging
import typing as typ

from .models import Invocation, PassthroughResult

logger = logging.getLogger(__name__)

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc


def parse_json_safely(data: bytes) -> dict[str, typ.Any] | None:
    """Return a JSON object parsed from *data* or ``None`` on failure.

    Returns
    -------
    dict[str, typing.Any] or None
        The decoded object when *data* contains a JSON object.
    """
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _build_payload[PayloadT](
    payload: dict[str, typ.Any],
    model: cabc.Callable[..., PayloadT],
    *,
    description: str,
) -> PayloadT | None:
    """Build *model* from *payload*, reporting malformed payloads once.

    Parameters
    ----------
    payload : dict[str, typing.Any]
        Decoded JSON object received from the IPC peer.
    model : collections.abc.Callable[..., PayloadT]
        IPC model whose keyword arguments the payload must supply.
    description : str
        Human-readable payload kind used when logging the failure.

    Returns
    -------
    PayloadT or None
        The built model, or ``None`` when the payload did not fit it.
    """
    try:
        return model(**payload)
    except TypeError:
        logger.exception(
            "IPC %s payload missing required fields: %r", description, payload
        )
        return None


def validate_invocation_payload(payload: dict[str, typ.Any]) -> Invocation | None:
    """Return an :class:`Invocation` if *payload* has the required fields.

    Returns
    -------
    Invocation or None
        The validated invocation, or ``None`` for malformed payloads.
    """
    return _build_payload(payload, Invocation, description="invocation")


def validate_passthrough_payload(
    payload: dict[str, typ.Any],
) -> PassthroughResult | None:
    """Return a :class:`PassthroughResult` for passthrough result payloads.

    Returns
    -------
    PassthroughResult or None
        The validated result, or ``None`` for malformed payloads.
    """
    return _build_payload(payload, PassthroughResult, description="passthrough")


__all__ = [
    "parse_json_safely",
    "validate_invocation_payload",
    "validate_passthrough_payload",
]
