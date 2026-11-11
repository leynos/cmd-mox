"""Shared JSON parsing helpers for IPC messaging."""

from __future__ import annotations

import json
import typing as typ


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


__all__ = ["parse_json_safely"]
