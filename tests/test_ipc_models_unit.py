"""Unit tests for IPC data models."""

from __future__ import annotations

import json

import pytest

from cmd_mox.ipc.models import (
    Invocation,
    PassthroughRequest,
    Response,
)


def test_invocation_to_dict_round_trip() -> None:
    """Invocation serialisation should be lossless for supported fields."""
    invocation = Invocation(
        command="cmd",
        args=["--flag"],
        stdin="input",
        env={"FOO": "1"},
        stdout="out",
        stderr="err",
        exit_code=2,
        invocation_id="abc",
    )

    payload = invocation.to_dict()

    assert payload == {
        "command": "cmd",
        "args": ["--flag"],
        "stdin": "input",
        "env": {"FOO": "1"},
        "stdout": "out",
        "stderr": "err",
        "exit_code": 2,
        "invocation_id": "abc",
    }


def test_passthrough_request_to_dict_includes_defaults() -> None:
    """Passthrough requests should expose all relevant fields."""
    request = PassthroughRequest(
        invocation_id="123",
        lookup_path="/bin/echo",
    )

    assert request.to_dict() == {
        "invocation_id": "123",
        "lookup_path": "/bin/echo",
        "extra_env": {},
        "timeout": 30.0,
    }


def test_response_from_payload_warns_on_invalid_env(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Response.from_payload should warn when env is not a mapping."""
    assert isinstance(caplog, pytest.LogCaptureFixture)
    payload = {
        "stdout": "out",
        "stderr": "",
        "exit_code": 0,
        "env": ["not", "a", "dict"],
    }

    with caplog.at_level("WARNING", logger="cmd_mox.ipc.models"):
        response = Response.from_payload(payload)

    assert response.env == {}
    assert any(
        "Payload 'env' is not a dict" in record.message for record in caplog.records
    )


def test_response_serialises_passthrough() -> None:
    """Responses should serialise passthrough requests when present."""
    request = PassthroughRequest(
        invocation_id="123",
        lookup_path="/bin/echo",
        extra_env={"A": "1"},
        timeout=4.2,
    )
    response = Response(stdout="", stderr="", exit_code=0, passthrough=request)

    payload = response.to_dict()

    assert json.loads(json.dumps(payload)) == {
        "stdout": "",
        "stderr": "",
        "exit_code": 0,
        "env": {},
        "passthrough": {
            "invocation_id": "123",
            "lookup_path": "/bin/echo",
            "extra_env": {"A": "1"},
            "timeout": 4.2,
        },
    }


def test_invocation_apply_updates_result_fields() -> None:
    """Invocation.apply should pull response status fields in-place."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    response = Response(stdout="new", stderr="err", exit_code=5)

    invocation.apply(response)

    assert invocation.stdout == "new"
    assert invocation.stderr == "err"
    assert invocation.exit_code == 5
