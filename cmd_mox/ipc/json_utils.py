"""Shared JSON parsing helpers for IPC messaging."""

from __future__ import annotations

import json
import logging
import typing as typ

from .models import Invocation, PassthroughResult

logger = logging.getLogger(__name__)


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
    return typ.cast("dict[str, typ.Any]", payload)


def validate_invocation_payload(payload: dict[str, typ.Any]) -> Invocation | None:
    """Return an :class:`Invocation` if *payload* has the required fields.

    Returns
    -------
    Invocation or None
        The validated invocation, or ``None`` for malformed payloads.
    """
    try:
        return Invocation(**payload)
    except TypeError:
        logger.exception("IPC payload missing required fields: %r", payload)
        return None


def validate_passthrough_payload(
    payload: dict[str, typ.Any],
) -> PassthroughResult | None:
    """Return a :class:`PassthroughResult` for passthrough result payloads.

    Returns
    -------
    PassthroughResult or None
        The validated result, or ``None`` for malformed payloads.
    """
    try:
        return PassthroughResult(**payload)
    except TypeError:
        logger.exception("IPC passthrough payload missing required fields: %r", payload)
        return None


__all__ = [
    "parse_json_safely",
    "validate_invocation_payload",
    "validate_passthrough_payload",
]
