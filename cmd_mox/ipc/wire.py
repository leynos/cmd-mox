"""Convert domain values to and from IPC wire payloads.

Use these functions at the transport boundary when sending or receiving
JSON-ready mappings; domain values do not encode themselves. Encoding accepts
trusted domain values and emits the established wire shapes. Decoding treats
its input as untrusted: :func:`decode_invocation`,
:func:`decode_passthrough_request`, :func:`decode_passthrough_result`, and
:func:`decode_response` check every field's type and raise
:class:`WireFormatError` when a payload does not match its model. Error messages
name a field path and an expected type but never include received values,
payload-supplied field names, environment keys, environment values, or list
elements.

Examples
--------
>>> invocation_to_payload(Invocation("git", [], "", {}))["command"]
'git'
>>> decode_invocation({"command": "git", "args": [], "stdin": "", "env": {}}).command
'git'
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ

from cmd_mox._dotted_paths import join_dotted_path
from cmd_mox._validators import validate_positive_finite_timeout
from cmd_mox.domain import Invocation, PassthroughRequest, PassthroughResult, Response


class WireFormatError(RuntimeError):
    """Raised when an IPC payload does not match its domain model.

    Messages carry a field path and an expected type only. They never contain
    received values, payload-supplied field names, environment keys, environment
    values, or list elements, so a caller may log a message without leaking
    untrusted payload content.
    """


def _fail(path: str, expected: str) -> typ.NoReturn:
    """Raise a payload-free format error for *path*."""
    msg = f"{path or 'payload'} must be {expected}"
    raise WireFormatError(msg)


def _require_payload(value: object, path: str) -> dict[str, typ.Any]:
    """Return a string-keyed copy of *value* or raise for a non-mapping."""
    if not isinstance(value, cabc.Mapping):
        _fail(path or "payload", "an object")
    payload: dict[str, typ.Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            _fail(path or "payload", "an object with string keys")
        payload[key] = item
    return payload


def _check_keys(
    payload: cabc.Mapping[str, typ.Any],
    *,
    path: str,
    required: frozenset[str],
    optional: frozenset[str],
) -> None:
    """Reject missing required fields and fields outside the model.

    Unknown field names are caller-supplied text, so the error reports only
    how many there are. Missing field names come from the model's own field
    set and are safe to name.

    Raises
    ------
    WireFormatError
        If a required field is absent or the payload carries unknown fields.
    """
    present = set(payload)
    if missing := required - present:
        msg = f"{path or 'payload'} is missing required field(s): "
        msg += ", ".join(sorted(missing))
        raise WireFormatError(msg)
    if unknown_count := len(present - required - optional):
        msg = f"{path or 'payload'} has {unknown_count} unknown field(s)"
        raise WireFormatError(msg)


def _require_str(value: object, path: str) -> str:
    """Return *value* as a string or raise."""
    if not isinstance(value, str):
        _fail(path, "a string")
    return value


def _require_optional_str(value: object, path: str) -> str | None:
    """Return *value* as a string, null, or raise."""
    if value is None or isinstance(value, str):
        return value
    _fail(path, "a string")


def _require_int(value: object, path: str) -> int:
    """Return *value* as an integer, rejecting booleans."""
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(path, "an integer")
    return value


def _require_str_list(value: object, path: str) -> list[str]:
    """Return a copy of *value* as a list of strings or raise."""
    if not isinstance(value, list):
        _fail(path, "a list of strings")
    for item in value:
        if not isinstance(item, str):
            _fail(path, "a list of strings")
    return list(value)


def _require_str_map(value: object, path: str) -> dict[str, str]:
    """Return a copy of *value* as a string-to-string mapping or raise.

    The failure message deliberately omits offending keys and values: both are
    caller-supplied environment data.

    Returns
    -------
    dict[str, str]
        A new mapping with validated string keys and values.
    """
    if not isinstance(value, cabc.Mapping):
        _fail(path, "an object with string keys and string values")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            _fail(path, "an object with string keys and string values")
        result[key] = item
    return result


def _require_timeout(value: object, path: str) -> float:
    """Validate *value* as a positive finite number and return a float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(path, "a positive finite number")
    try:
        validate_positive_finite_timeout(typ.cast("float", value))
        return float(value)
    except (OverflowError, TypeError, ValueError):
        # An integer too large for a float makes math.isfinite raise
        # OverflowError rather than the documented TypeError or ValueError.
        _fail(path, "a positive finite number")


_INVOCATION_REQUIRED: typ.Final[frozenset[str]] = frozenset({
    "command",
    "args",
    "stdin",
    "env",
})
_INVOCATION_OPTIONAL: typ.Final[frozenset[str]] = frozenset({
    "stdout",
    "stderr",
    "exit_code",
    "invocation_id",
})
_PASSTHROUGH_RESULT_FIELDS: typ.Final[frozenset[str]] = frozenset({
    "invocation_id",
    "stdout",
    "stderr",
    "exit_code",
})
_PASSTHROUGH_REQUEST_REQUIRED: typ.Final[frozenset[str]] = frozenset({
    "invocation_id",
    "lookup_path",
})
_PASSTHROUGH_REQUEST_OPTIONAL: typ.Final[frozenset[str]] = frozenset({
    "extra_env",
    "timeout",
})
_RESPONSE_OPTIONAL: typ.Final[frozenset[str]] = frozenset({
    "stdout",
    "stderr",
    "exit_code",
    "env",
    "passthrough",
})


def invocation_to_payload(invocation: Invocation) -> dict[str, typ.Any]:
    """Encode an invocation as a JSON-ready IPC payload.

    Parameters
    ----------
    invocation : Invocation
        Domain invocation to encode.

    Returns
    -------
    dict[str, typing.Any]
        A copy of the invocation fields using the established wire names.

    Notes
    -----
    ``invocation_id`` is omitted when it is ``None`` to preserve the existing
    wire contract.
    """
    payload: dict[str, typ.Any] = {
        "command": invocation.command,
        "args": list(invocation.args),
        "stdin": invocation.stdin,
        "env": dict(invocation.env),
        "stdout": invocation.stdout,
        "stderr": invocation.stderr,
        "exit_code": invocation.exit_code,
    }
    if invocation.invocation_id is not None:
        payload["invocation_id"] = invocation.invocation_id
    return payload


def passthrough_to_payload(
    passthrough: PassthroughRequest | PassthroughResult,
) -> dict[str, typ.Any]:
    """Encode a passthrough request or result as an IPC payload.

    Parameters
    ----------
    passthrough : PassthroughRequest or PassthroughResult
        Domain value to encode.

    Returns
    -------
    dict[str, typing.Any]
        The JSON-ready fields for the request or result.
    """
    match passthrough:
        case PassthroughRequest():
            return {
                "invocation_id": passthrough.invocation_id,
                "lookup_path": passthrough.lookup_path,
                "extra_env": dict(passthrough.extra_env),
                "timeout": passthrough.timeout,
            }
        case PassthroughResult():
            return {
                "invocation_id": passthrough.invocation_id,
                "stdout": passthrough.stdout,
                "stderr": passthrough.stderr,
                "exit_code": passthrough.exit_code,
            }
    typ.assert_never(passthrough)


def response_to_payload(response: Response) -> dict[str, typ.Any]:
    """Encode a domain response as a JSON-ready IPC payload.

    Parameters
    ----------
    response : Response
        Domain response to encode.

    Returns
    -------
    dict[str, typing.Any]
        The response fields and, when present, its nested passthrough request.
    """
    payload: dict[str, typ.Any] = {
        "stdout": response.stdout,
        "stderr": response.stderr,
        "exit_code": response.exit_code,
        "env": dict(response.env),
    }
    if response.passthrough is not None:
        payload["passthrough"] = passthrough_to_payload(response.passthrough)
    return payload


def decode_invocation(payload: object) -> Invocation:
    """Decode an invocation payload, validating every field.

    Parameters
    ----------
    payload : object
        Decoded, untrusted JSON value from the wire.

    Returns
    -------
    Invocation
        The validated invocation.

    Raises
    ------
    WireFormatError
        If *payload* is not a mapping, has missing or unknown fields, or a
        field has the wrong type.
    """  # ruff: ignore[docstring-extraneous-exception] - _fail raises this caller-visible failure.
    data = _require_payload(payload, "")
    _check_keys(
        data,
        path="",
        required=_INVOCATION_REQUIRED,
        optional=_INVOCATION_OPTIONAL,
    )
    return Invocation(
        command=_require_str(data["command"], "command"),
        args=_require_str_list(data["args"], "args"),
        stdin=_require_str(data["stdin"], "stdin"),
        env=_require_str_map(data["env"], "env"),
        stdout=_require_str(data.get("stdout", ""), "stdout"),
        stderr=_require_str(data.get("stderr", ""), "stderr"),
        exit_code=_require_int(data.get("exit_code", 0), "exit_code"),
        invocation_id=_require_optional_str(data.get("invocation_id"), "invocation_id"),
    )


def decode_passthrough_result(payload: object) -> PassthroughResult:
    """Decode a passthrough result payload, validating every field.

    Parameters
    ----------
    payload : object
        Decoded, untrusted JSON value from the wire.

    Returns
    -------
    PassthroughResult
        The validated passthrough result.

    Raises
    ------
    WireFormatError
        If *payload* is not a mapping, has missing or unknown fields, or a
        field has the wrong type.
    """  # ruff: ignore[docstring-extraneous-exception] - _fail raises this caller-visible failure.
    data = _require_payload(payload, "")
    _check_keys(
        data,
        path="",
        required=_PASSTHROUGH_RESULT_FIELDS,
        optional=frozenset(),
    )
    return PassthroughResult(
        invocation_id=_require_str(data["invocation_id"], "invocation_id"),
        stdout=_require_str(data["stdout"], "stdout"),
        stderr=_require_str(data["stderr"], "stderr"),
        exit_code=_require_int(data["exit_code"], "exit_code"),
    )


def _decode_passthrough_request(payload: object, *, path: str) -> PassthroughRequest:
    """Decode a passthrough request under the dotted *path* prefix."""
    data = _require_payload(payload, path)
    _check_keys(
        data,
        path=path,
        required=_PASSTHROUGH_REQUEST_REQUIRED,
        optional=_PASSTHROUGH_REQUEST_OPTIONAL,
    )
    return PassthroughRequest(
        invocation_id=_require_str(
            data["invocation_id"], join_dotted_path(path, "invocation_id")
        ),
        lookup_path=_require_str(
            data["lookup_path"], join_dotted_path(path, "lookup_path")
        ),
        extra_env=_require_str_map(
            data.get("extra_env", {}), join_dotted_path(path, "extra_env")
        ),
        timeout=_require_timeout(
            data.get("timeout", 30.0), join_dotted_path(path, "timeout")
        ),
    )


def decode_passthrough_request(payload: object) -> PassthroughRequest:
    """Decode a passthrough directive payload, validating every field.

    Parameters
    ----------
    payload : object
        Decoded, untrusted JSON value from the wire.

    Returns
    -------
    PassthroughRequest
        The validated passthrough request.

    Raises
    ------
    WireFormatError
        If *payload* is not a mapping, has missing or unknown fields, or a
        field has the wrong type.
    """  # ruff: ignore[docstring-extraneous-exception] - _fail raises this caller-visible failure.
    return _decode_passthrough_request(payload, path="")


def decode_response(payload: object) -> Response:
    """Decode a response payload, validating every field and directive.

    A present, non-null ``passthrough`` value must decode in full as a
    passthrough request. A malformed directive is an error rather than a
    response that silently behaves like an ordinary reply.

    Parameters
    ----------
    payload : object
        Decoded, untrusted JSON value from the wire.

    Returns
    -------
    Response
        The validated response.

    Raises
    ------
    WireFormatError
        If *payload* is not a mapping, has unknown fields, a field has the
        wrong type, or a present passthrough directive is malformed.
    """  # ruff: ignore[docstring-extraneous-exception] - _fail raises this caller-visible failure.
    data = _require_payload(payload, "")
    _check_keys(data, path="", required=frozenset(), optional=_RESPONSE_OPTIONAL)
    passthrough_value = data.get("passthrough")
    passthrough = (
        None
        if passthrough_value is None
        else _decode_passthrough_request(passthrough_value, path="passthrough")
    )
    return Response(
        stdout=_require_str(data.get("stdout", ""), "stdout"),
        stderr=_require_str(data.get("stderr", ""), "stderr"),
        exit_code=_require_int(data.get("exit_code", 0), "exit_code"),
        env=_require_str_map(data.get("env", {}), "env"),
        passthrough=passthrough,
    )


# Deliberate compatibility alias: the migration note and existing callers use
# this name, and it must keep the strict decoder's behaviour.
# pylint: disable-next=trivial-alias-wrapper
def response_from_payload(payload: object) -> Response:
    """Decode a response payload; retained compatibility name.

    This is a strict alias of :func:`decode_response` and has identical
    validation and failure behaviour.

    Parameters
    ----------
    payload : object
        Decoded, untrusted JSON value from the wire.

    Returns
    -------
    Response
        The validated response.

    Raises
    ------
    WireFormatError
        If *payload* does not describe a valid response.
    """  # ruff: ignore[docstring-extraneous-exception] - decode_response raises this caller-visible failure.
    return decode_response(payload)


__all__ = [
    "WireFormatError",
    "decode_invocation",
    "decode_passthrough_request",
    "decode_passthrough_result",
    "decode_response",
    "invocation_to_payload",
    "passthrough_to_payload",
    "response_from_payload",
    "response_to_payload",
]
