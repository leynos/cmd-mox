"""Unit tests for IPC JSON helpers."""

from __future__ import annotations

import json

from cmd_mox.domain import Invocation, PassthroughResult
from cmd_mox.ipc.json_utils import (
    parse_json_safely,
    validate_invocation_payload,
    validate_passthrough_payload,
)


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


def test_validate_invocation_payload_returns_model() -> None:
    """validate_invocation_payload should build Invocation instances."""
    payload = {
        "command": "cmd",
        "args": [],
        "stdin": "",
        "env": {},
    }

    result = validate_invocation_payload(payload)

    assert isinstance(result, Invocation), "Assertion failed"
    assert result.command == "cmd", "Assertion failed"


def test_validate_invocation_payload_handles_missing_fields() -> None:
    """Missing invocation fields should return None."""
    assert validate_invocation_payload({}) is None, "Assertion failed"


def test_validate_passthrough_payload_returns_model() -> None:
    """validate_passthrough_payload should build PassthroughResult instances."""
    payload = {
        "invocation_id": "123",
        "stdout": "out",
        "stderr": "err",
        "exit_code": 0,
    }

    result = validate_passthrough_payload(payload)

    assert isinstance(result, PassthroughResult), "Assertion failed"
    assert result.invocation_id == "123", "Assertion failed"


def test_validate_passthrough_payload_handles_missing_fields() -> None:
    """Missing passthrough fields should return None."""
    assert validate_passthrough_payload({}) is None, "Assertion failed"
