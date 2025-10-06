"""Unit tests for IPC JSON helpers."""

from __future__ import annotations

import json

import pytest

from cmd_mox.ipc.json_utils import (
    parse_json_safely,
    validate_invocation_payload,
    validate_passthrough_payload,
)
from cmd_mox.ipc.models import Invocation, PassthroughResult


def test_parse_json_safely_accepts_valid_object() -> None:
    """parse_json_safely should return dictionaries for valid JSON objects."""
    payload = {"kind": "invocation"}
    data = json.dumps(payload).encode("utf-8")

    assert parse_json_safely(data) == payload


def test_parse_json_safely_rejects_non_objects() -> None:
    """parse_json_safely should return None for non-object JSON values."""
    assert parse_json_safely(b"[]") is None
    assert parse_json_safely(b"123") is None


def test_validate_invocation_payload_returns_model() -> None:
    """validate_invocation_payload should build Invocation instances."""
    payload = {
        "command": "cmd",
        "args": [],
        "stdin": "",
        "env": {},
    }

    result = validate_invocation_payload(payload)

    assert isinstance(result, Invocation)
    assert result.command == "cmd"


def test_validate_invocation_payload_handles_missing_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing invocation fields should be logged and return None."""
    assert isinstance(caplog, pytest.LogCaptureFixture)
    with caplog.at_level("ERROR", logger="cmd_mox.ipc.json_utils"):
        assert validate_invocation_payload({}) is None


def test_validate_passthrough_payload_returns_model() -> None:
    """validate_passthrough_payload should build PassthroughResult instances."""
    payload = {
        "invocation_id": "123",
        "stdout": "out",
        "stderr": "err",
        "exit_code": 0,
    }

    result = validate_passthrough_payload(payload)

    assert isinstance(result, PassthroughResult)
    assert result.invocation_id == "123"


def test_validate_passthrough_payload_handles_missing_fields(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Missing passthrough fields should be logged and return None."""
    assert isinstance(caplog, pytest.LogCaptureFixture)
    with caplog.at_level("ERROR", logger="cmd_mox.ipc.json_utils"):
        assert validate_passthrough_payload({}) is None
