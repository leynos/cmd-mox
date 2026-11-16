"""Shared JSON parsing helpers for IPC messaging."""

from __future__ import annotations

import json
import typing as typ


class JSONParseError(ValueError):
    """Raised when raw IPC bytes cannot be decoded as bounded JSON.

    The message is deliberately fixed. Parser exceptions can embed a snippet
    of the untrusted payload, so callers must not log the cause.
    """


def _reject_duplicate_keys(pairs: list[tuple[str, typ.Any]]) -> dict[str, typ.Any]:
    """Build an object from *pairs*, rejecting a repeated key at any depth."""
    result: dict[str, typ.Any] = {}
    for key, value in pairs:
        if key in result:
            msg = "duplicate object key"
            raise JSONParseError(msg)
        result[key] = value
    return result


def decode_json(data: bytes) -> object:
    """Decode one JSON value from *data*, rejecting duplicate object keys.

    Parameters
    ----------
    data : bytes
        Raw wire bytes, treated as untrusted.

    Returns
    -------
    object
        The decoded JSON value, narrowed by the caller after validation.

    Raises
    ------
    JSONParseError
        If *data* is not UTF-8, is not valid JSON, repeats a key in any
        object, nests too deeply, or contains an integer beyond Python's
        string-conversion limit.

    Notes
    -----
    A parser that accepted duplicate keys would let a later value silently
    replace an earlier one before the strict wire decoders see it. For
    example, a malformed ``passthrough`` directive followed by
    ``"passthrough": null`` would become an ordinary response.
    """
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except JSONParseError:
        raise
    except (UnicodeDecodeError, ValueError, RecursionError) as exc:
        # ValueError covers JSONDecodeError, duplicate keys, and oversized
        # integer strings; RecursionError covers excessive nesting.
        raise JSONParseError from exc


def parse_json_safely(data: bytes) -> dict[str, typ.Any] | None:
    """Return a JSON object parsed from *data* or ``None`` on failure.

    Duplicate keys anywhere in the document are rejected, as are parser
    failures such as excessive nesting or oversized integers.

    Returns
    -------
    dict[str, typing.Any] or None
        The decoded object when *data* contains a JSON object.
    """
    try:
        payload = decode_json(data)
    except JSONParseError:
        return None
    if not isinstance(payload, dict):
        return None
    return typ.cast("dict[str, typ.Any]", payload)


__all__ = ["JSONParseError", "decode_json", "parse_json_safely"]
