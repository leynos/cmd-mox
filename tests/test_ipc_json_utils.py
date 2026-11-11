"""Unit tests for IPC JSON helpers."""

from __future__ import annotations

import json

from cmd_mox.ipc.json_utils import parse_json_safely


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
