"""Strict decoder contract tests for IPC wire payloads."""

from __future__ import annotations

import copy
import math
import typing as typ

import pytest

from cmd_mox.domain import Invocation, PassthroughRequest, PassthroughResult, Response
from cmd_mox.ipc.wire import (
    WireFormatError,
    decode_invocation,
    decode_passthrough_request,
    decode_passthrough_result,
    decode_response,
    invocation_to_payload,
    passthrough_to_payload,
    response_to_payload,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_INVOCATION: dict[str, typ.Any] = {
    "command": "git",
    "args": ["status"],
    "stdin": "input",
    "env": {"A": "1"},
    "stdout": "out",
    "stderr": "err",
    "exit_code": 2,
    "invocation_id": "abc",
}
_PASSTHROUGH_RESULT: dict[str, typ.Any] = {
    "invocation_id": "abc",
    "stdout": "out",
    "stderr": "err",
    "exit_code": 2,
}
_PASSTHROUGH_REQUEST: dict[str, typ.Any] = {
    "invocation_id": "abc",
    "lookup_path": "/bin/echo",
    "extra_env": {"A": "1"},
    "timeout": 1.5,
}
_RESPONSE: dict[str, typ.Any] = {
    "stdout": "out",
    "stderr": "err",
    "exit_code": 2,
    "env": {"A": "1"},
    "passthrough": _PASSTHROUGH_REQUEST,
}


def _with_field(
    base: dict[str, typ.Any], field: str, value: object
) -> dict[str, typ.Any]:
    """Return a deep copy of *base* with *field* replaced."""
    payload = copy.deepcopy(base)
    payload[field] = value
    return payload


def _with_passthrough_field(
    base: dict[str, typ.Any], field: str, value: object
) -> dict[str, typ.Any]:
    """Return a deep copy of *base* with a nested passthrough field replaced."""
    payload = copy.deepcopy(base)
    payload["passthrough"][field] = value
    return payload


@pytest.mark.parametrize(
    ("decoder", "payload", "expected"),
    [
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "command", 5),
            "command must be a string",
            id="invocation-command-int",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "command", None),
            "command must be a string",
            id="invocation-command-null",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "args", "abc"),
            "args must be a list of strings",
            id="invocation-args-string",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "args", None),
            "args must be a list of strings",
            id="invocation-args-null",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "args", [1]),
            "args must be a list of strings",
            id="invocation-args-int-element",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "args", [None]),
            "args must be a list of strings",
            id="invocation-args-null-element",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "stdin", 5),
            "stdin must be a string",
            id="invocation-stdin-int",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "env", None),
            "env must be an object with string keys and string values",
            id="invocation-env-null",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "env", []),
            "env must be an object with string keys and string values",
            id="invocation-env-list",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "env", {"A": 1}),
            "env must be an object with string keys and string values",
            id="invocation-env-int-value",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "env", {1: "v"}),
            "env must be an object with string keys and string values",
            id="invocation-env-int-key",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "stdout", 5),
            "stdout must be a string",
            id="invocation-stdout-int",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "stderr", 5),
            "stderr must be a string",
            id="invocation-stderr-int",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "exit_code", "x"),
            "exit_code must be an integer",
            id="invocation-exit-code-string",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "exit_code", value=True),
            "exit_code must be an integer",
            id="invocation-exit-code-bool",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "exit_code", 1.5),
            "exit_code must be an integer",
            id="invocation-exit-code-float",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "exit_code", None),
            "exit_code must be an integer",
            id="invocation-exit-code-null",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "invocation_id", 5),
            "invocation_id must be a string",
            id="invocation-id-int",
        ),
        pytest.param(
            decode_invocation,
            _with_field(_INVOCATION, "invocation_id", []),
            "invocation_id must be a string",
            id="invocation-id-list",
        ),
        pytest.param(
            decode_passthrough_result,
            _with_field(_PASSTHROUGH_RESULT, "invocation_id", 5),
            "invocation_id must be a string",
            id="result-id-int",
        ),
        pytest.param(
            decode_passthrough_result,
            _with_field(_PASSTHROUGH_RESULT, "invocation_id", None),
            "invocation_id must be a string",
            id="result-id-null",
        ),
        pytest.param(
            decode_passthrough_result,
            _with_field(_PASSTHROUGH_RESULT, "stdout", 5),
            "stdout must be a string",
            id="result-stdout-int",
        ),
        pytest.param(
            decode_passthrough_result,
            _with_field(_PASSTHROUGH_RESULT, "stderr", 5),
            "stderr must be a string",
            id="result-stderr-int",
        ),
        pytest.param(
            decode_passthrough_result,
            _with_field(_PASSTHROUGH_RESULT, "stderr", None),
            "stderr must be a string",
            id="result-stderr-null",
        ),
        pytest.param(
            decode_passthrough_result,
            _with_field(_PASSTHROUGH_RESULT, "exit_code", "x"),
            "exit_code must be an integer",
            id="result-exit-code-string",
        ),
        pytest.param(
            decode_passthrough_result,
            _with_field(_PASSTHROUGH_RESULT, "exit_code", value=True),
            "exit_code must be an integer",
            id="result-exit-code-bool",
        ),
        pytest.param(
            decode_passthrough_result,
            _with_field(_PASSTHROUGH_RESULT, "exit_code", None),
            "exit_code must be an integer",
            id="result-exit-code-null",
        ),
        pytest.param(
            decode_passthrough_request,
            _with_field(_PASSTHROUGH_REQUEST, "invocation_id", 5),
            "invocation_id must be a string",
            id="request-id-int",
        ),
        pytest.param(
            decode_passthrough_request,
            _with_field(_PASSTHROUGH_REQUEST, "invocation_id", None),
            "invocation_id must be a string",
            id="request-id-null",
        ),
        pytest.param(
            decode_passthrough_request,
            _with_field(_PASSTHROUGH_REQUEST, "lookup_path", 5),
            "lookup_path must be a string",
            id="request-lookup-path-int",
        ),
        pytest.param(
            decode_passthrough_request,
            _with_field(_PASSTHROUGH_REQUEST, "lookup_path", None),
            "lookup_path must be a string",
            id="request-lookup-path-null",
        ),
        pytest.param(
            decode_passthrough_request,
            _with_field(_PASSTHROUGH_REQUEST, "extra_env", None),
            "extra_env must be an object with string keys and string values",
            id="request-extra-env-null",
        ),
        pytest.param(
            decode_passthrough_request,
            _with_field(_PASSTHROUGH_REQUEST, "extra_env", []),
            "extra_env must be an object with string keys and string values",
            id="request-extra-env-list",
        ),
        pytest.param(
            decode_passthrough_request,
            _with_field(_PASSTHROUGH_REQUEST, "extra_env", {"A": 1}),
            "extra_env must be an object with string keys and string values",
            id="request-extra-env-int-value",
        ),
        pytest.param(
            decode_passthrough_request,
            _with_field(_PASSTHROUGH_REQUEST, "extra_env", {1: "v"}),
            "extra_env must be an object with string keys and string values",
            id="request-extra-env-int-key",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "stdout", 5),
            "stdout must be a string",
            id="response-stdout-int",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "stdout", None),
            "stdout must be a string",
            id="response-stdout-null",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "stderr", 5),
            "stderr must be a string",
            id="response-stderr-int",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "stderr", None),
            "stderr must be a string",
            id="response-stderr-null",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "exit_code", "x"),
            "exit_code must be an integer",
            id="response-exit-code-string",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "exit_code", value=True),
            "exit_code must be an integer",
            id="response-exit-code-bool",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "exit_code", None),
            "exit_code must be an integer",
            id="response-exit-code-null",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "env", None),
            "env must be an object with string keys and string values",
            id="response-env-null",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "env", []),
            "env must be an object with string keys and string values",
            id="response-env-list",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "env", {"A": 1}),
            "env must be an object with string keys and string values",
            id="response-env-int-value",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "passthrough", "directive"),
            "passthrough must be an object",
            id="response-passthrough-string",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "passthrough", []),
            "passthrough must be an object",
            id="response-passthrough-list",
        ),
        pytest.param(
            decode_response,
            _with_field(_RESPONSE, "passthrough", 5),
            "passthrough must be an object",
            id="response-passthrough-int",
        ),
        pytest.param(
            decode_response,
            _with_passthrough_field(_RESPONSE, "lookup_path", 5),
            "passthrough.lookup_path must be a string",
            id="response-passthrough-lookup-path-int",
        ),
        pytest.param(
            decode_response,
            _with_passthrough_field(_RESPONSE, "extra_env", {"A": 1}),
            (
                "passthrough.extra_env must be an object with string keys "
                "and string values"
            ),
            id="response-passthrough-extra-env-int-value",
        ),
        pytest.param(
            decode_response,
            _with_passthrough_field(_RESPONSE, "timeout", "soon"),
            "passthrough.timeout must be a positive finite number",
            id="response-passthrough-timeout-string",
        ),
    ],
)
def test_decoders_reject_wrong_field_types(
    decoder: cabc.Callable[[object], object], payload: dict[str, typ.Any], expected: str
) -> None:
    """Every model field rejects a value of the wrong type."""
    with pytest.raises(WireFormatError) as excinfo:
        decoder(payload)

    assert expected in str(excinfo.value), "Wrong field path or expected type"


@pytest.mark.parametrize(
    "decoder",
    [
        decode_invocation,
        decode_passthrough_result,
        decode_passthrough_request,
        decode_response,
    ],
)
@pytest.mark.parametrize("payload", [None, [], "text", 5, [("a", "b")]])
def test_decoders_reject_non_mapping_payloads(
    decoder: cabc.Callable[[object], object], payload: object
) -> None:
    """A payload that is not a mapping is rejected before field checks."""
    with pytest.raises(WireFormatError, match="payload must be an object"):
        decoder(payload)


@pytest.mark.parametrize(
    "decoder",
    [
        decode_invocation,
        decode_passthrough_result,
        decode_passthrough_request,
        decode_response,
    ],
)
def test_decoders_reject_non_string_payload_keys(
    decoder: cabc.Callable[[object], object],
) -> None:
    """A mapping with a non-string key is rejected without naming the key."""
    with pytest.raises(
        WireFormatError, match="payload must be an object with string keys"
    ):
        decoder({1: "value"})


@pytest.mark.parametrize(
    ("decoder", "base", "field"),
    [
        pytest.param(decode_invocation, _INVOCATION, "command", id="command"),
        pytest.param(decode_invocation, _INVOCATION, "args", id="args"),
        pytest.param(decode_invocation, _INVOCATION, "stdin", id="stdin"),
        pytest.param(decode_invocation, _INVOCATION, "env", id="env"),
        pytest.param(
            decode_passthrough_result,
            _PASSTHROUGH_RESULT,
            "invocation_id",
            id="result-invocation-id",
        ),
        pytest.param(
            decode_passthrough_result,
            _PASSTHROUGH_RESULT,
            "stdout",
            id="result-stdout",
        ),
        pytest.param(
            decode_passthrough_result,
            _PASSTHROUGH_RESULT,
            "stderr",
            id="result-stderr",
        ),
        pytest.param(
            decode_passthrough_result,
            _PASSTHROUGH_RESULT,
            "exit_code",
            id="result-exit-code",
        ),
        pytest.param(
            decode_passthrough_request,
            _PASSTHROUGH_REQUEST,
            "invocation_id",
            id="request-invocation-id",
        ),
        pytest.param(
            decode_passthrough_request,
            _PASSTHROUGH_REQUEST,
            "lookup_path",
            id="request-lookup-path",
        ),
    ],
)
def test_decoders_reject_missing_required_fields(
    decoder: cabc.Callable[[object], object], base: dict[str, typ.Any], field: str
) -> None:
    """A missing required field is named in the error."""
    payload = copy.deepcopy(base)
    del payload[field]

    with pytest.raises(WireFormatError) as excinfo:
        decoder(payload)

    message = str(excinfo.value)
    assert "missing required field(s)" in message, "Missing field was not reported"
    assert field in message, "Missing field name was not reported"


@pytest.mark.parametrize(
    ("decoder", "base"),
    [
        pytest.param(decode_invocation, _INVOCATION, id="invocation"),
        pytest.param(
            decode_passthrough_result, _PASSTHROUGH_RESULT, id="passthrough-result"
        ),
        pytest.param(
            decode_passthrough_request, _PASSTHROUGH_REQUEST, id="passthrough-request"
        ),
        pytest.param(decode_response, _RESPONSE, id="response"),
    ],
)
def test_decoders_reject_unknown_fields(
    decoder: cabc.Callable[[object], object], base: dict[str, typ.Any]
) -> None:
    """An unknown field is named in the error."""
    payload = {**copy.deepcopy(base), "unexpected": 1}

    with pytest.raises(WireFormatError) as excinfo:
        decoder(payload)

    message = str(excinfo.value)
    assert "unknown field(s)" in message, "Unknown field was not reported"
    assert "unexpected" in message, "Unknown field name was not reported"


def test_response_rejects_unknown_nested_passthrough_field() -> None:
    """An unknown field inside a directive is reported with its path."""
    payload = _with_passthrough_field(_RESPONSE, "unexpected", 1)

    with pytest.raises(WireFormatError) as excinfo:
        decode_response(payload)

    message = str(excinfo.value)
    assert "passthrough has unknown field(s)" in message, "Wrong nested path"
    assert "unexpected" in message, "Unknown nested field was not reported"


def test_response_rejects_passthrough_missing_lookup_path() -> None:
    """A directive missing its required field is rejected, not dropped."""
    payload = {"stdout": "", "passthrough": {"invocation_id": "a"}}

    with pytest.raises(WireFormatError) as excinfo:
        decode_response(payload)

    message = str(excinfo.value)
    assert "passthrough is missing required field(s)" in message, "Wrong path"
    assert "lookup_path" in message, "Missing directive field was not reported"


@pytest.mark.parametrize(
    "timeout",
    [
        pytest.param(True, id="bool"),
        pytest.param("5", id="string"),
        pytest.param(0, id="zero"),
        pytest.param(-1.0, id="negative"),
        pytest.param(math.nan, id="nan"),
        pytest.param(math.inf, id="inf"),
        pytest.param(None, id="null"),
    ],
)
def test_passthrough_request_rejects_invalid_timeouts(timeout: object) -> None:
    """A timeout must be a positive finite number."""
    payload = _with_field(_PASSTHROUGH_REQUEST, "timeout", timeout)

    with pytest.raises(
        WireFormatError, match="timeout must be a positive finite number"
    ):
        decode_passthrough_request(payload)


def test_passthrough_request_accepts_integer_timeout_as_float() -> None:
    """An integer timeout is accepted and stored as a float."""
    payload = _with_field(_PASSTHROUGH_REQUEST, "timeout", 5)

    request = decode_passthrough_request(payload)

    assert request.timeout == pytest.approx(5.0), "Integer timeout was not converted"
    assert isinstance(request.timeout, float), "Timeout must be stored as a float"


def test_invocation_defaults_apply_for_omitted_optional_fields() -> None:
    """Omitted optional invocation fields take their dataclass defaults."""
    invocation = decode_invocation({
        "command": "git",
        "args": [],
        "stdin": "",
        "env": {},
    })

    assert invocation == Invocation("git", [], "", {}), "Defaults changed"


def test_passthrough_request_defaults_apply_for_omitted_optional_fields() -> None:
    """Omitted directive fields take their dataclass defaults."""
    request = decode_passthrough_request({
        "invocation_id": "abc",
        "lookup_path": "/bin/echo",
    })

    assert request == PassthroughRequest("abc", "/bin/echo"), "Defaults changed"
    assert request.timeout == pytest.approx(30.0), "Default timeout changed"


def test_response_defaults_apply_for_an_empty_payload() -> None:
    """An empty response payload decodes to the default response."""
    assert decode_response({}) == Response(), "Response defaults changed"


def test_invocation_accepts_null_invocation_id() -> None:
    """A null invocation identifier is the documented absent value."""
    payload = _with_field(_INVOCATION, "invocation_id", None)

    assert decode_invocation(payload).invocation_id is None, "Null ID was rejected"


def test_response_accepts_null_passthrough() -> None:
    """A null passthrough is the documented absent directive."""
    payload = _with_field(_RESPONSE, "passthrough", None)

    assert decode_response(payload).passthrough is None, "Null directive was rejected"


def test_error_messages_do_not_include_payload_values() -> None:
    """A rejected payload's values never appear in the error message."""
    probe = "synthetic-secret-value"
    payload = {
        "command": probe,
        "args": [probe, 5],
        "stdin": "",
        "env": {"SECRET_KEY": probe},
    }

    with pytest.raises(WireFormatError) as excinfo:
        decode_invocation(payload)

    message = str(excinfo.value)
    assert probe not in message, "Payload value leaked into the error"
    assert "SECRET_KEY" not in message, "Environment key leaked into the error"
    assert "args" in message, "Field path was not reported"


def test_environment_errors_omit_keys_and_values() -> None:
    """A malformed environment reports only its field path."""
    payload = _with_field(_INVOCATION, "env", {"SECRET_KEY": 123})

    with pytest.raises(WireFormatError) as excinfo:
        decode_invocation(payload)

    message = str(excinfo.value)
    assert "SECRET_KEY" not in message, "Environment key leaked into the error"
    assert "123" not in message, "Environment value leaked into the error"


@pytest.mark.parametrize(
    ("decoder", "base"),
    [
        pytest.param(decode_invocation, _INVOCATION, id="invocation"),
        pytest.param(
            decode_passthrough_result, _PASSTHROUGH_RESULT, id="passthrough-result"
        ),
        pytest.param(
            decode_passthrough_request, _PASSTHROUGH_REQUEST, id="passthrough-request"
        ),
        pytest.param(decode_response, _RESPONSE, id="response"),
    ],
)
def test_decoders_do_not_mutate_the_input_payload(
    decoder: cabc.Callable[[object], object], base: dict[str, typ.Any]
) -> None:
    """Decoding leaves the supplied payload unchanged."""
    payload = copy.deepcopy(base)
    original = copy.deepcopy(payload)

    decoder(payload)

    assert payload == original, "Input payload was mutated"


def test_decoded_collections_are_independent_copies() -> None:
    """Decoded lists and mappings do not alias the input payload."""
    payload = {
        "command": "git",
        "args": ["status"],
        "stdin": "",
        "env": {"A": "1"},
    }

    invocation = decode_invocation(payload)
    payload["args"].append("--short")
    payload["env"]["A"] = "2"

    assert invocation.args == ["status"], "Decoded args alias the input"
    assert invocation.env == {"A": "1"}, "Decoded env aliases the input"

    invocation.args.append("--long")
    invocation.env["B"] = "3"

    assert payload["args"] == ["status", "--short"], "Input args were mutated"
    assert payload["env"] == {"A": "2"}, "Input env was mutated"


def test_passthrough_extra_env_is_an_independent_copy() -> None:
    """A decoded directive's extra environment does not alias the input."""
    payload = copy.deepcopy(_PASSTHROUGH_REQUEST)

    request = decode_passthrough_request(payload)
    payload["extra_env"]["A"] = "2"

    assert request.extra_env == {"A": "1"}, "Decoded extra_env aliases the input"


@pytest.mark.parametrize(
    ("model", "encoder", "decoder"),
    [
        pytest.param(
            Invocation(
                command="git",
                args=["status"],
                stdin="input",
                env={"A": "1"},
                stdout="out",
                stderr="err",
                exit_code=2,
                invocation_id="abc",
            ),
            invocation_to_payload,
            decode_invocation,
            id="invocation",
        ),
        pytest.param(
            PassthroughResult(
                invocation_id="abc", stdout="out", stderr="err", exit_code=2
            ),
            passthrough_to_payload,
            decode_passthrough_result,
            id="passthrough-result",
        ),
        pytest.param(
            PassthroughRequest(
                invocation_id="abc",
                lookup_path="/bin/echo",
                extra_env={"A": "1"},
                timeout=1.5,
            ),
            passthrough_to_payload,
            decode_passthrough_request,
            id="passthrough-request",
        ),
        pytest.param(
            Response(
                stdout="out",
                stderr="err",
                exit_code=2,
                env={"A": "1"},
                passthrough=PassthroughRequest(
                    invocation_id="abc",
                    lookup_path="/bin/echo",
                    extra_env={"A": "1"},
                    timeout=1.5,
                ),
            ),
            response_to_payload,
            decode_response,
            id="response",
        ),
    ],
)
def test_non_default_round_trips(
    model: object,
    encoder: cabc.Callable[[object], object],
    decoder: cabc.Callable[[object], object],
) -> None:
    """Encoding then decoding a non-default model returns an equal value."""
    assert decoder(encoder(model)) == model, "Round trip changed the model"


def test_reproduction_response_rejects_wrong_types() -> None:
    """The reported response payload is rejected instead of coerced."""
    with pytest.raises(WireFormatError):
        decode_response({"stdout": 1, "exit_code": "x", "env": None})


def test_reproduction_malformed_passthrough_is_rejected() -> None:
    """The reported malformed directive is rejected instead of dropped."""
    with pytest.raises(WireFormatError):
        decode_response({"stdout": "", "passthrough": {"invocation_id": "a"}})


def test_reproduction_invocation_rejects_wrong_types() -> None:
    """The reported invocation body is rejected instead of dispatched."""
    with pytest.raises(WireFormatError):
        decode_invocation({
            "command": 5,
            "args": "abc",
            "stdin": None,
            "env": [1, 2],
        })
