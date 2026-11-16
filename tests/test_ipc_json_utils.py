"""Unit tests for IPC JSON helpers."""

from __future__ import annotations

import json

import pytest

from cmd_mox.ipc.json_utils import JSONParseError, decode_json, parse_json_safely

_DEEP_JSON = b"[" * 100_000 + b"]" * 100_000
_OVERSIZED_INTEGER_JSON = b'{"value": ' + b"9" * 5_000 + b"}"


def test_parse_json_safely_accepts_valid_object() -> None:
    """parse_json_safely should return dictionaries for valid JSON objects."""
    payload = {"kind": "invocation"}
    data = json.dumps(payload).encode("utf-8")

    assert parse_json_safely(data) == payload, "Assertion failed"


def test_parse_json_safely_rejects_non_objects() -> None:
    """parse_json_safely should return None for non-object JSON values."""
    assert parse_json_safely(b"[]") is None, "Assertion failed"
    assert parse_json_safely(b"123") is None, "Assertion failed"


def test_parse_json_safely_handles_invalid_utf8() -> None:
    """Non-UTF-8 payloads should be treated as parse failures."""
    # ``b"\x80"`` is not valid UTF-8 and previously raised ``UnicodeDecodeError``.
    assert parse_json_safely(b"\x80") is None, "Assertion failed"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(
            b'{"kind": "invocation", "kind": "passthrough_result"}',
            id="top-level-duplicate-key",
        ),
        pytest.param(b'{"section": {"key": 1, "key": 2}}', id="nested-duplicate-key"),
        pytest.param(_OVERSIZED_INTEGER_JSON, id="oversized-integer"),
        pytest.param(_DEEP_JSON, id="excessive-nesting"),
    ],
)
def test_unbounded_json_is_a_bounded_parse_failure(data: bytes) -> None:
    """Duplicate keys and parser limits surface as one bounded error."""
    with pytest.raises(JSONParseError):
        decode_json(data)

    assert parse_json_safely(data) is None, "Unsafe input survived the safe parser"


def test_duplicate_key_error_does_not_echo_the_key() -> None:
    """A duplicate-key failure must not copy untrusted key text into its message."""
    with pytest.raises(JSONParseError) as excinfo:
        decode_json(b'{"s3cr3t-key": 1, "s3cr3t-key": 2}')

    assert "s3cr3t-key" not in str(excinfo.value), "Payload key reached the error"
