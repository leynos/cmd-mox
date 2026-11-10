"""Strict, path-aware validation helpers for fixture JSON documents.

The helpers in this module raise :class:`ValueError` messages in the fixture
schema error format so callers can report precisely which field failed.  They
are deliberately dependency-light: importing this module must not pull in the
fixture or scrubber models, which would create an import cycle.
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

#: Prefix shared by every schema validation error raised by this module.
SCHEMA_ERROR_PREFIX: typ.Final[str] = "Invalid fixture schema"


def _join_path(parent: str, key: str) -> str:
    """Join *parent* and *key* into a dotted schema path."""
    return f"{parent}.{key}" if parent else key


def _missing_field(path: str) -> ValueError:
    """Build the error for a required field that is absent."""
    return ValueError(f"{SCHEMA_ERROR_PREFIX}: missing required field '{path}'")


def _wrong_type(path: str, kind: str, value: object) -> ValueError:
    """Build the error for a value of the wrong type."""
    where = f"{path}: " if path else ""
    return ValueError(
        f"{SCHEMA_ERROR_PREFIX}: {where}expected {kind}, got {type(value).__name__}"
    )


def _require_value(
    mapping: cabc.Mapping[str, object], key: str, *, path: str
) -> tuple[str, object]:
    """Return the schema path and value for *key*, raising when it is absent."""
    field = _join_path(path, key)
    if key not in mapping:
        raise _missing_field(field)
    return field, mapping[key]


def _require_object(value: object, path: str) -> cabc.Mapping[str, object]:
    """Return *value* as a mapping, raising when it is not an object."""
    if not isinstance(value, cabc.Mapping):
        raise _wrong_type(path, "object", value)
    return value


def _require_type(
    mapping: cabc.Mapping[str, object],
    key: str,
    *,
    path: str,
    expected: type,
) -> object:
    """Return a required field whose value is an instance of *expected*."""
    field, value = _require_value(mapping, key, path=path)
    if not isinstance(value, expected):
        raise _wrong_type(field, expected.__name__, value)
    return value


def _require_str(
    mapping: cabc.Mapping[str, object], key: str, *, path: str = ""
) -> str:
    """Return a required string field."""
    return typ.cast("str", _require_type(mapping, key, path=path, expected=str))


def _optional_str(
    mapping: cabc.Mapping[str, object], key: str, *, path: str = ""
) -> str | None:
    """Return an optional string field, mapping a missing key or null to ``None``."""
    field = _join_path(path, key)
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _wrong_type(field, "str", value)
    return value


def _require_int(
    mapping: cabc.Mapping[str, object], key: str, *, path: str = ""
) -> int:
    """Return a required integer field, rejecting booleans and floats."""
    field, value = _require_value(mapping, key, path=path)
    # ``bool`` is an ``int`` subclass, and JSON numbers may arrive as floats;
    # neither is a valid fixture integer.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _wrong_type(field, "int", value)
    return value


def _require_list(
    mapping: cabc.Mapping[str, object], key: str, *, path: str = ""
) -> list[object]:
    """Return a required list field."""
    return typ.cast(
        "list[object]", _require_type(mapping, key, path=path, expected=list)
    )


def _require_str_list(
    mapping: cabc.Mapping[str, object], key: str, *, path: str = ""
) -> list[str]:
    """Return a required list of strings, reporting the offending index."""
    items = _require_list(mapping, key, path=path)
    field = _join_path(path, key)
    strings: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, str):
            raise _wrong_type(f"{field}[{index}]", "str", item)
        strings.append(item)
    return strings


def _require_str_mapping(
    mapping: cabc.Mapping[str, object], key: str, *, path: str = ""
) -> dict[str, str]:
    """Return a required string-to-string mapping, reporting the offending key."""
    field, value = _require_value(mapping, key, path=path)
    if not isinstance(value, cabc.Mapping):
        raise _wrong_type(field, "object", value)
    strings: dict[str, str] = {}
    for map_key, map_value in value.items():
        if not isinstance(map_key, str):
            raise _wrong_type(f"{field}[{map_key!r}]", "str key", map_key)
        if not isinstance(map_value, str):
            raise _wrong_type(f"{field}[{map_key!r}]", "str", map_value)
        strings[map_key] = map_value
    return strings
