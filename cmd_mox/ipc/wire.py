"""Convert domain values to and from IPC wire payloads."""

from __future__ import annotations

import logging
import typing as typ

from cmd_mox.domain import Invocation, PassthroughRequest, PassthroughResult, Response

logger = logging.getLogger(__name__)


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


def passthrough_request_to_payload(
    request: PassthroughRequest,
) -> dict[str, typ.Any]:
    """Encode a passthrough request as a JSON-ready IPC payload.

    Parameters
    ----------
    request : PassthroughRequest
        Domain request to encode.

    Returns
    -------
    dict[str, typing.Any]
        A copy of all request fields, including default values.
    """
    return {
        "invocation_id": request.invocation_id,
        "lookup_path": request.lookup_path,
        "extra_env": dict(request.extra_env),
        "timeout": request.timeout,
    }


def passthrough_result_to_payload(
    result: PassthroughResult,
) -> dict[str, typ.Any]:
    """Encode a passthrough result as a JSON-ready IPC payload.

    Parameters
    ----------
    result : PassthroughResult
        Domain result to encode.

    Returns
    -------
    dict[str, typing.Any]
        The result fields using the established wire names.
    """
    return {
        "invocation_id": result.invocation_id,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
    }


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
        payload["passthrough"] = passthrough_request_to_payload(response.passthrough)
    return payload


def invocation_from_payload(payload: dict[str, typ.Any]) -> Invocation:
    """Build an invocation from its decoded IPC payload.

    Parameters
    ----------
    payload : dict[str, typing.Any]
        Decoded payload containing invocation fields.

    Returns
    -------
    Invocation
        The domain invocation represented by *payload*.

    Notes
    -----
    The domain constructor reports missing or unexpected fields with
    ``TypeError``.
    """
    return Invocation(**payload)


def passthrough_result_from_payload(
    payload: dict[str, typ.Any],
) -> PassthroughResult:
    """Build a passthrough result from its decoded IPC payload.

    Parameters
    ----------
    payload : dict[str, typing.Any]
        Decoded payload containing passthrough result fields.

    Returns
    -------
    PassthroughResult
        The domain result represented by *payload*.

    Notes
    -----
    The domain constructor reports missing or unexpected fields with
    ``TypeError``.
    """
    return PassthroughResult(**payload)


def _build_passthrough_request(
    payload: dict[str, typ.Any],
) -> PassthroughRequest | None:
    """Build a passthrough request when its required fields are present.

    Returns
    -------
    PassthroughRequest or None
        The directive, or ``None`` when required fields are missing.
    """
    try:
        invocation_id = str(payload["invocation_id"])
        lookup_path = str(payload["lookup_path"])
    except KeyError:
        logger.warning("Passthrough directive missing required fields")
        return None

    extra_env_raw = payload.get("extra_env", {})
    extra_env: dict[str, str] = {}
    if isinstance(extra_env_raw, dict):
        extra_env = {str(key): str(value) for key, value in extra_env_raw.items()}

    timeout_raw = payload.get("timeout", 30.0)
    try:
        timeout = float(timeout_raw)
    except (TypeError, ValueError):
        logger.debug("Invalid passthrough timeout; using default")
        timeout = 30.0

    return PassthroughRequest(
        invocation_id=invocation_id,
        lookup_path=lookup_path,
        extra_env=extra_env,
        timeout=timeout,
    )


def response_from_payload(payload: dict[str, typ.Any]) -> Response:
    """Build a response from its decoded IPC payload.

    Parameters
    ----------
    payload : dict[str, typing.Any]
        Decoded response payload from an IPC peer.

    Returns
    -------
    Response
        The domain response represented by *payload*.

    Raises
    ------
    RuntimeError
        If the response fields cannot construct a valid domain response.
    """
    passthrough_payload = payload.get("passthrough")
    passthrough: PassthroughRequest | None = None
    if isinstance(passthrough_payload, dict):
        passthrough = _build_passthrough_request(passthrough_payload)

    response_payload = payload.copy()
    response_payload.pop("passthrough", None)
    env = response_payload.get("env")
    if env is not None and not isinstance(env, dict):
        logger.warning(
            "Payload 'env' is not a dict (type: %s)",
            type(env).__name__,
        )
        response_payload["env"] = {}
    try:
        response = Response(**response_payload)
    except TypeError as exc:
        msg = "Invalid response payload from IPC server"
        raise RuntimeError(msg) from exc
    response.passthrough = passthrough
    return response


__all__ = [
    "invocation_from_payload",
    "invocation_to_payload",
    "passthrough_request_to_payload",
    "passthrough_result_from_payload",
    "passthrough_result_to_payload",
    "response_from_payload",
    "response_to_payload",
]
