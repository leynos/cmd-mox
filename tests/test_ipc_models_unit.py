"""Unit tests for IPC data models."""

from __future__ import annotations

import json
import typing as typ

import pytest

import cmd_mox
import cmd_mox.ipc.models as ipc_models
from cmd_mox import ipc
from cmd_mox.domain import (
    Invocation,
    PassthroughRequest,
    PassthroughResult,
    Response,
)
from cmd_mox.ipc.wire import (
    WireFormatError,
    invocation_to_payload,
    passthrough_to_payload,
    response_from_payload,
    response_to_payload,
)

if typ.TYPE_CHECKING:
    from syrupy.assertion import SnapshotAssertion


def test_public_imports_reexport_domain_types() -> None:
    """Existing IPC and top-level imports retain the domain class identities."""
    assert (
        cmd_mox.Invocation is ipc.Invocation is ipc_models.Invocation is Invocation
    ), "Invocation re-export changed"
    assert cmd_mox.Response is ipc.Response is ipc_models.Response is Response, (
        "Response re-export changed"
    )
    assert (
        ipc.PassthroughRequest is ipc_models.PassthroughRequest is PassthroughRequest
    ), "PassthroughRequest re-export changed"
    assert ipc.PassthroughResult is ipc_models.PassthroughResult is PassthroughResult, (
        "PassthroughResult re-export changed"
    )


def test_invocation_to_payload_round_trip(snapshot: SnapshotAssertion) -> None:
    """Invocation wire encoding should preserve supported fields."""
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

    assert invocation_to_payload(invocation) == snapshot, "Invocation payload changed"


def test_passthrough_request_to_payload_includes_defaults() -> None:
    """Passthrough requests should expose all relevant fields."""
    request = PassthroughRequest(
        invocation_id="123",
        lookup_path="/bin/echo",
    )

    assert passthrough_to_payload(request) == {
        "invocation_id": "123",
        "lookup_path": "/bin/echo",
        "extra_env": {},
        "timeout": 30.0,
    }, "Assertion failed"


def test_passthrough_result_to_payload() -> None:
    """Passthrough results retain their established wire fields."""
    result = PassthroughResult(
        invocation_id="123", stdout="out", stderr="err", exit_code=2
    )
    expected = {
        "invocation_id": "123",
        "stdout": "out",
        "stderr": "err",
        "exit_code": 2,
    }

    assert passthrough_to_payload(result) == expected, "Result payload changed"


def test_response_from_payload_rejects_invalid_env_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Response decoding rejects a non-mapping env without logging its contents."""
    payload = {
        "stdout": "out",
        "stderr": "",
        "exit_code": 0,
        "env": ["synthetic-secret-value"],
    }

    with (
        caplog.at_level("DEBUG", logger="cmd_mox.ipc.wire"),
        pytest.raises(WireFormatError) as excinfo,
    ):
        response_from_payload(payload)

    assert "env must be an object" in str(excinfo.value), "Wrong field diagnostic"
    assert all(
        "synthetic-secret-value" not in record.getMessage() for record in caplog.records
    ), "Malformed environment contents reached the log"


def test_invalid_passthrough_does_not_log_environment(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Malformed directives must not disclose environment values in logs."""
    payload = {
        "passthrough": {
            "lookup_path": "/bin/echo",
            "extra_env": {"TOKEN": "synthetic-secret-value"},
        }
    }

    with (
        caplog.at_level("DEBUG", logger="cmd_mox.ipc.wire"),
        pytest.raises(WireFormatError) as excinfo,
    ):
        response_from_payload(payload)

    assert "passthrough is missing required field(s)" in str(excinfo.value), (
        "Malformed directive was not rejected"
    )
    assert all(
        "synthetic-secret-value" not in record.getMessage() for record in caplog.records
    ), "Passthrough environment contents reached the log"


def test_response_serialises_passthrough(snapshot: SnapshotAssertion) -> None:
    """Responses should serialise passthrough requests when present."""
    request = PassthroughRequest(
        invocation_id="123",
        lookup_path="/bin/echo",
        extra_env={"A": "1"},
        timeout=4.2,
    )
    response = Response(stdout="", stderr="", exit_code=0, passthrough=request)

    assert json.loads(json.dumps(response_to_payload(response))) == snapshot, (
        "Passthrough response payload changed"
    )


def test_response_from_payload_rejects_invalid_fields() -> None:
    """Response decoding rejects fields outside the response model."""
    with pytest.raises(WireFormatError, match="unknown field"):
        response_from_payload({"unexpected": "field"})


def test_response_from_payload_decodes_passthrough_without_mutating_input() -> None:
    """A valid nested directive decodes without mutating the supplied payload."""
    payload: dict[str, typ.Any] = {
        "stdout": "out",
        "passthrough": {
            "invocation_id": "123",
            "lookup_path": "/bin/echo",
            "extra_env": {"COUNT": "2"},
            "timeout": 4.5,
        },
    }
    original = json.loads(json.dumps(payload))

    response = response_from_payload(payload)

    assert response.passthrough == PassthroughRequest(
        invocation_id="123",
        lookup_path="/bin/echo",
        extra_env={"COUNT": "2"},
        timeout=4.5,
    ), "Passthrough directive changed"
    assert payload == original, "Input payload was mutated"


def test_invocation_apply_updates_result_fields() -> None:
    """Invocation.apply should pull response status fields in-place."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    response = Response(stdout="new", stderr="err", exit_code=5)

    invocation.apply(resp=response)

    assert invocation.stdout == "new", "Assertion failed"
    assert invocation.stderr == "err", "Assertion failed"
    assert invocation.exit_code == 5, "Assertion failed"
