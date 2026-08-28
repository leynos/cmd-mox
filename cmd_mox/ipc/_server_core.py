"""Transport-neutral IPC request handling shared by the transports.

This module holds the server lifecycle scaffolding, request parsing, dispatch,
and observability helpers used by both the Unix-socket transport
(:mod:`cmd_mox.ipc.server`) and the Windows named-pipe transport
(:mod:`cmd_mox.ipc.named_pipe`). It deliberately imports neither transport so
that both can depend on it without creating an import cycle.
"""

from __future__ import annotations

import abc
import collections.abc as cabc
import dataclasses as dc
import json
import logging
import threading
import time
import typing as typ
from pathlib import Path

from cmd_mox._validators import (
    validate_optional_timeout,
    validate_positive_finite_timeout,
)
from cmd_mox.environment import EnvironmentManager

from . import _observability
from .constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from .json_utils import (
    parse_json_safely,
    validate_invocation_payload,
    validate_passthrough_payload,
)
from .models import Invocation, PassthroughResult, Response

if typ.TYPE_CHECKING:
    from types import TracebackType

logger = logging.getLogger(__name__)

type _RequestValidator = cabc.Callable[
    [dict[str, typ.Any]], Invocation | PassthroughResult | None
]


class _ServerLifecycle[BackendT](abc.ABC):
    """Shared lifecycle management for IPC transports."""

    def __init__(
        self,
        socket_path: Path,
        timeout: float,
        accept_timeout: float | None,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self.accept_timeout = accept_timeout or min(0.1, timeout / 10)
        self._server: BackendT | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def __enter__(self) -> typ.Self:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def start(self) -> None:
        """Start the backend transport.

        Raises
        ------
        RuntimeError
            If the backend has already been started.
        """
        with self._lock:
            if self._thread:
                msg = "IPC server already started"
                raise RuntimeError(msg)

            self._prepare_backend_start()
            self._export_environment()
            server, thread = self._create_backend()
            self._server = server
            self._thread = thread

        self._start_backend_thread(thread)
        self._wait_until_ready()

    def stop(self) -> None:
        """Stop the backend transport."""
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None

        self._stop_backend(server)
        self._join_backend_thread(thread)
        self._post_stop_cleanup()

    @abc.abstractmethod
    def _create_backend(self) -> tuple[BackendT, threading.Thread]: ...

    def _prepare_backend_start(self) -> None:  # ruff: ignore[empty-method-without-abstract-decorator] - base lifecycle hook intentionally has no default implementation
        """Perform any setup required before starting the backend server."""

    def _export_environment(self) -> None:  # ruff: ignore[empty-method-without-abstract-decorator] - base lifecycle hook intentionally has no default implementation
        """Export environment variables for client processes."""

    @staticmethod
    def _start_backend_thread(thread: threading.Thread) -> None:
        thread.start()

    def _wait_until_ready(self) -> None:  # ruff: ignore[empty-method-without-abstract-decorator] - base lifecycle hook intentionally has no default implementation
        """Wait for the backend server to be ready to accept connections."""

    # ruff: ignore[empty-method-without-abstract-decorator] - base lifecycle hook intentionally has no default implementation
    @staticmethod
    def _stop_backend(server: BackendT | None) -> None:
        """Stop the backend server instance."""

    def _join_backend_thread(self, thread: threading.Thread | None) -> None:
        if thread is None:
            return
        thread.join(self.timeout)

    def _post_stop_cleanup(self) -> None:  # ruff: ignore[empty-method-without-abstract-decorator] - base lifecycle hook intentionally has no default implementation
        """Perform cleanup after the backend server has stopped."""


@dc.dataclass(slots=True)
class IPCHandlers:
    """Optional callbacks customising :class:`BaseIPCServer` behaviour."""

    handler: cabc.Callable[[Invocation], Response] | None = None
    passthrough_handler: cabc.Callable[[PassthroughResult], Response] | None = None


@dc.dataclass(slots=True)
class TimeoutConfig:
    """Timeout configuration forwarded by :class:`CallbackIPCServer`."""

    timeout: float = 5.0
    accept_timeout: float | None = None

    def __post_init__(self) -> None:
        """Validate timeout values to catch misconfiguration early."""
        validate_positive_finite_timeout(self.timeout)
        validate_optional_timeout(self.accept_timeout, name="accept_timeout")


class _BaseIPCServer[BackendT](_ServerLifecycle[BackendT]):
    """Shared handler wiring for IPC transports."""

    def __init__(
        self,
        socket_path: Path,
        timeout: float = 5.0,
        accept_timeout: float | None = None,
        *,
        handlers: IPCHandlers | None = None,
    ) -> None:
        validate_positive_finite_timeout(timeout)
        validate_optional_timeout(accept_timeout, name="accept_timeout")
        super().__init__(Path(socket_path), timeout, accept_timeout)
        handlers = handlers or IPCHandlers()
        self._handler = handlers.handler
        self._passthrough_handler = handlers.passthrough_handler

    @staticmethod
    def _dispatch[DispatchArg: (Invocation, PassthroughResult)](
        handler: cabc.Callable[[DispatchArg], Response] | None,
        argument: DispatchArg,
        *,
        default: cabc.Callable[[DispatchArg], Response],
        error_builder: cabc.Callable[[DispatchArg, Exception], RuntimeError]
        | None = None,
    ) -> Response:
        """Invoke *handler* when provided, otherwise fall back to *default*.

        Returns
        -------
        Response
            The handler's response, or *default*'s response when no handler is
            configured.

        Raises
        ------
        KeyboardInterrupt
            Propagated unchanged from *handler*.
        SystemExit
            Propagated unchanged from *handler*.
        RuntimeError
            The wrapped handler failure built by *error_builder*, when one is
            supplied.
        """  # ruff: ignore[docstring-extraneous-exception] - error_builder propagates this caller-visible failure.
        if handler is None:
            return default(argument)
        if error_builder is None:
            return handler(argument)
        try:
            return handler(argument)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise error_builder(argument, exc) from exc

    @staticmethod
    def _default_invocation_response(invocation: Invocation) -> Response:
        """Echo the command name when no handler overrides the behaviour.

        Returns
        -------
        Response
            A response whose stdout is the invoked command name.
        """
        return Response(stdout=invocation.command)

    @staticmethod
    def _raise_unhandled_passthrough(result: PassthroughResult) -> Response:
        """Raise when passthrough results lack a configured handler.

        Raises
        ------
        RuntimeError
            Always, naming the unhandled invocation.
        """
        msg = f"Unhandled passthrough result for {result.invocation_id}"
        raise RuntimeError(msg)

    @staticmethod
    def _build_passthrough_error(
        result: PassthroughResult, exc: Exception
    ) -> RuntimeError:
        """Create the wrapped passthrough error surfaced to callers.

        Returns
        -------
        RuntimeError
            An error naming the invocation and the underlying failure.
        """
        msg = f"Exception in passthrough handler for {result.invocation_id}: {exc}"
        return RuntimeError(msg)

    def _export_environment(self) -> None:
        env_mgr = EnvironmentManager.get_active_manager()
        if env_mgr is not None:
            env_mgr.export_ipc_environment(timeout=self.timeout)

    def handle_invocation(self, invocation: Invocation) -> Response:
        """Process invocations using the configured handler when available.

        Returns
        -------
        Response
            The configured handler's response or the default echo response.
        """
        return self._dispatch(
            self._handler,
            invocation,
            default=self._default_invocation_response,
        )

    def handle_passthrough_result(self, result: PassthroughResult) -> Response:
        """Handle passthrough results via the configured callback when provided.

        Returns
        -------
        Response
            The callback's acknowledgement response.
        """
        return self._dispatch(
            self._passthrough_handler,
            result,
            default=self._raise_unhandled_passthrough,
            error_builder=self._build_passthrough_error,
        )


type _RequestProcessor = typ.Literal["handle_invocation", "handle_passthrough_result"]
type _DispatchOutcome = typ.Literal["success", "invalid_request", "handler_error"]

_REQUEST_HANDLERS: dict[str, tuple[_RequestValidator, _RequestProcessor]] = {
    KIND_INVOCATION: (validate_invocation_payload, "handle_invocation"),
    KIND_PASSTHROUGH_RESULT: (
        validate_passthrough_payload,
        "handle_passthrough_result",
    ),
}


DISPATCH_OPERATION: typ.Final[str] = "ipc.dispatch"
_DISPATCH_MESSAGE: typ.Final[str] = "IPC dispatch outcome"

# Envelope fields wrap the model body on the wire and are stripped before
# validation; passing them to ``Invocation``/``PassthroughResult`` would raise
# ``TypeError`` and reject every request.
_ENVELOPE_FIELDS: typ.Final[frozenset[str]] = frozenset({"kind", "correlation_id"})
# Correlation identifiers are opaque and client-supplied, so cap their length to
# keep the observability dimension bounded.
_MAX_CORRELATION_ID_LENGTH: typ.Final[int] = 64


@dc.dataclass(slots=True)
class ParsedRequest:
    """Parsed request containing payload and dispatch metadata.

    ``payload`` holds only the model body: the ``kind`` and ``correlation_id``
    envelope fields are stripped during parsing and surfaced as attributes.
    """

    payload: dict[str, typ.Any]
    kind: str
    validator: _RequestValidator
    processor: _RequestProcessor
    correlation_id: str | None = None

    def validate(self) -> Invocation | PassthroughResult | None:
        """Run the validator associated with this request payload.

        Returns
        -------
        Invocation, PassthroughResult, or None
            The validated request object, or ``None`` for invalid payloads.
        """
        return self.validator(self.payload)


def _decode_payload(raw: bytes) -> dict[str, typ.Any] | None:
    """Decode raw request bytes into a mapping, logging malformed input once.

    Returns
    -------
    dict[str, typ.Any] or None
        The decoded payload, or ``None`` when *raw* is not a JSON mapping.
    """
    payload = parse_json_safely(raw)
    if payload is not None:
        return payload

    try:
        _ = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.error("IPC received malformed JSON")  # ruff: ignore[error-instead-of-exception] - decode-error messages embed a snippet of the untrusted payload, so the traceback must not be logged
        return None

    logger.error("IPC payload is not a mapping")
    return None


def _extract_correlation_id(payload: dict[str, typ.Any]) -> str | None:
    """Return the bounded ``correlation_id`` envelope field, if usable.

    Returns
    -------
    str or None
        The identifier, or ``None`` when absent, empty, over-long, or not a
        string. No server-side substitute is manufactured.
    """
    value = payload.get("correlation_id")
    if isinstance(value, str) and 0 < len(value) <= _MAX_CORRELATION_ID_LENGTH:
        return value
    return None


def _parse_payload(raw: bytes) -> ParsedRequest | None:
    """Split a raw request into its envelope metadata and model body.

    The ``kind`` and ``correlation_id`` envelope fields are removed from the
    body so the model constructors only ever see their own fields.

    Returns
    -------
    ParsedRequest or None
        The parsed request, or ``None`` when the payload is undecodable or
        names an unknown kind.
    """
    payload = _decode_payload(raw)
    if payload is None:
        return None

    kind = str(payload.get("kind", KIND_INVOCATION))
    handler_entry = _REQUEST_HANDLERS.get(kind)
    if handler_entry is None:
        logger.error("Unknown IPC payload kind")
        return None

    body = {key: value for key, value in payload.items() if key not in _ENVELOPE_FIELDS}
    validator, processor = handler_entry
    return ParsedRequest(
        payload=body,
        kind=kind,
        validator=validator,
        processor=processor,
        correlation_id=_extract_correlation_id(payload),
    )


def _encode_response(response: Response) -> bytes:
    return json.dumps(response.to_dict()).encode("utf-8")


@dc.dataclass(slots=True)
class _DispatchRecord:
    """Bounded metadata describing one IPC dispatch outcome.

    Every field is safe to log: nothing derived from request payloads, command
    arguments, standard streams, environments, socket paths, or exception
    messages may be added here.
    """

    kind: str
    request: Invocation | PassthroughResult | None
    outcome: _DispatchOutcome
    duration_ms: float
    error_category: str | None = None
    correlation_id: str | None = None
    transport: _observability.Transport | None = None

    def to_event(self) -> _observability.IPCEvent:
        """Render the record as a shared observability event.

        Returns
        -------
        _observability.IPCEvent
            The bounded event describing this dispatch.
        """
        return _observability.IPCEvent(
            operation=DISPATCH_OPERATION,
            transport=self.transport,
            kind=self.kind,
            outcome=self.outcome,
            error_category=self.error_category,
            duration_ms=self.duration_ms,
            correlation_id=self.correlation_id,
        )

    def extra_fields(self) -> dict[str, str | int | float]:
        """Return bounded fields the shared event does not model.

        Returns
        -------
        dict[str, str | int | float]
            The passthrough-only invocation identifier, when one exists.
        """
        # Invocation requests carry no server-assigned identifier, so only
        # passthrough results contribute one; never manufacture a substitute.
        if isinstance(self.request, PassthroughResult):
            return {"invocation_id": self.request.invocation_id}
        return {}


def _emit_dispatch_outcome(record: _DispatchRecord) -> None:
    """Emit bounded metadata for one IPC dispatch outcome."""
    _observability.emit(
        record.to_event(),
        logger=logger,
        extra=record.extra_fields(),
        message=_DISPATCH_MESSAGE,
    )


def _resolve_correlation_id(
    parsed: ParsedRequest, obj: Invocation | PassthroughResult | None
) -> str | None:
    """Return the identifier correlating this dispatch with the client record.

    Older shims omit the envelope field, so fall back to the validated model's
    ``invocation_id`` when it has one and omit the dimension otherwise.

    Returns
    -------
    str or None
        The correlation identifier, or ``None`` when none is available.
    """
    if parsed.correlation_id is not None:
        return parsed.correlation_id
    invocation_id = getattr(obj, "invocation_id", None)
    return invocation_id if isinstance(invocation_id, str) else None


def _request_pipeline(
    server: _BaseIPCServer[typ.Any],
    raw: bytes,
    transport: _observability.Transport | None = None,
) -> bytes | None:
    """Parse, validate, dispatch, and encode an IPC request in order.

    Returns
    -------
    bytes or None
        The encoded response, or ``None`` when the request was unparseable or
        failed validation.
    """
    # Scope the measurement to the whole pipeline so parsing, validation, and
    # hook execution are all attributed to the dispatch record.
    started = time.perf_counter()
    parsed = _parse_payload(raw)
    if parsed is None:
        return None

    obj = parsed.validate()
    if obj is None:
        _emit_dispatch_outcome(
            _DispatchRecord(
                kind=parsed.kind,
                request=None,
                outcome="invalid_request",
                duration_ms=_observability.elapsed_ms(started),
                error_category="ValidationError",
                correlation_id=parsed.correlation_id,
                transport=transport,
            )
        )
        return None

    response, error_category = _execute_request(server, parsed.processor, obj)
    outcome: _DispatchOutcome = "success"
    if error_category is not None:
        outcome = "handler_error"
    _emit_dispatch_outcome(
        _DispatchRecord(
            kind=parsed.kind,
            request=obj,
            outcome=outcome,
            duration_ms=_observability.elapsed_ms(started),
            error_category=error_category,
            correlation_id=_resolve_correlation_id(parsed, obj),
            transport=transport,
        )
    )
    return _encode_response(response)


def _raise_invalid_request_dispatch(
    processor: _RequestProcessor, obj: Invocation | PassthroughResult
) -> typ.NoReturn:
    """Raise when the request registry maps a validator to the wrong hook.

    Raises
    ------
    TypeError
        Always, naming the mismatched processor and payload type.
    """
    msg = f"Request processor {processor!r} does not match validated "
    msg += f"payload {type(obj).__name__}"
    raise TypeError(msg)


def _execute_request(
    server: _BaseIPCServer[typ.Any],
    processor: _RequestProcessor,
    obj: Invocation | PassthroughResult,
) -> tuple[Response, str | None]:
    try:
        match processor, obj:
            case "handle_invocation", Invocation() as invocation:
                return server.handle_invocation(invocation), None
            case "handle_passthrough_result", PassthroughResult() as result:
                return server.handle_passthrough_result(result), None
            case _:
                return _raise_invalid_request_dispatch(processor, obj)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # ruff: ignore[blind-except] - IPC converts arbitrary application handler failures into responses.
        message = str(exc) or exc.__class__.__name__
        return Response(stderr=message, exit_code=1), exc.__class__.__name__
