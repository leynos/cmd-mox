"""Unit tests for IPC data models."""

from __future__ import annotations

import json
import typing as typ

import pytest

from cmd_mox.ipc.models import (
    Invocation,
    PassthroughRequest,
    Response,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def test_invocation_to_dict_round_trip(snapshot: SnapshotAssertion) -> None:
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

    assert invocation.to_dict() == snapshot, "Invocation payload changed"


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
    }, "Assertion failed"


def test_response_from_payload_warns_on_invalid_env(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Response.from_payload should warn when env is not a mapping."""
    assert isinstance(caplog, pytest.LogCaptureFixture), "Assertion failed"
    payload = {
        "stdout": "out",
        "stderr": "",
        "exit_code": 0,
        "env": ["not", "a", "dict"],
    }

    with caplog.at_level("WARNING", logger="cmd_mox.ipc.models"):
        response = Response.from_payload(payload)

    assert not response.env, "Assertion failed"
    assert any(
        "Payload 'env' is not a dict" in record.message for record in caplog.records
    ), "Assertion failed"


def test_response_serialises_passthrough(snapshot: SnapshotAssertion) -> None:
    """Responses should serialise passthrough requests when present."""
    request = PassthroughRequest(
        invocation_id="123",
        lookup_path="/bin/echo",
        extra_env={"A": "1"},
        timeout=4.2,
    )
    response = Response(stdout="", stderr="", exit_code=0, passthrough=request)

    assert json.loads(json.dumps(response.to_dict())) == snapshot, (
        "Passthrough response payload changed"
    )


def test_invocation_apply_updates_result_fields() -> None:
    """Invocation.apply should pull response status fields in-place."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    response = Response(stdout="new", stderr="err", exit_code=5)

    invocation.apply(response)

    assert invocation.stdout == "new", "Assertion failed"
    assert invocation.stderr == "err", "Assertion failed"
    assert invocation.exit_code == 5, "Assertion failed"
