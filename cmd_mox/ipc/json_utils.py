"""Shared JSON parsing helpers for IPC messaging."""

from __future__ import annotations

import json
import logging
import typing as t

from .models import Invocation, PassthroughResult

logger = logging.getLogger(__name__)


def parse_json_safely(data: bytes) -> dict[str, t.Any] | None:
    """Return a JSON object parsed from *data* or ``None`` on failure."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return t.cast("dict[str, t.Any]", payload)


def validate_invocation_payload(payload: dict[str, t.Any]) -> Invocation | None:
    """Return an :class:`Invocation` if *payload* has the required fields."""
    try:
        return Invocation(**payload)  # type: ignore[arg-type]
    except TypeError:
        logger.exception("IPC payload missing required fields: %r", payload)
        return None


def validate_passthrough_payload(
    payload: dict[str, t.Any],
) -> PassthroughResult | None:
    """Return a :class:`PassthroughResult` for passthrough result payloads."""
    try:
        return PassthroughResult(**payload)  # type: ignore[arg-type]
    except TypeError:
        logger.exception("IPC passthrough payload missing required fields: %r", payload)
        return None


__all__ = [
    "parse_json_safely",
    "validate_invocation_payload",
    "validate_passthrough_payload",
]
