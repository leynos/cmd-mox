"""IPC servers for CmdMox shims (Unix sockets and Windows named pipes)."""

from __future__ import annotations

import abc
import collections.abc as cabc
import contextlib
import dataclasses as dc
import importlib
import json
import logging
import socketserver
import threading
import typing as typ
from pathlib import Path

from cmd_mox import _path_utils as path_utils
from cmd_mox._validators import (
    validate_optional_timeout,
    validate_positive_finite_timeout,
)
from cmd_mox.environment import EnvironmentManager

from .constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from .json_utils import (
    parse_json_safely,
    validate_invocation_payload,
    validate_passthrough_payload,
)
from .models import Invocation, PassthroughResult, Response
from .socket_utils import cleanup_stale_socket, wait_for_socket


def _create_unsupported_unix_server() -> type[socketserver.BaseServer]:
    class _UnsupportedUnixServer(socketserver.BaseServer):
        """Placeholder that raises when Unix sockets are requested on Windows."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            msg = "Unix domain socket servers are unavailable on Windows"
            raise RuntimeError(msg)

    return typ.cast("type[socketserver.BaseServer]", _UnsupportedUnixServer)


def _resolve_unix_server_base() -> type[socketserver.BaseServer]:
    if path_utils.IS_WINDOWS:
        return _create_unsupported_unix_server()
    threading_server = getattr(socketserver, "ThreadingUnixStreamServer", None)
    if threading_server is not None:
        return typ.cast("type[socketserver.BaseServer]", threading_server)
    unix_server = getattr(socketserver, "UnixStreamServer", None)
    if unix_server is not None:

        class _ThreadingUnixCompat(
            socketserver.ThreadingMixIn,
            unix_server,
        ):
            """Threading shim for platforms lacking ThreadingUnixStreamServer."""

            pass

        return typ.cast("type[socketserver.BaseServer]", _ThreadingUnixCompat)
    msg = "Unix domain socket servers are not supported on this platform"
    raise RuntimeError(msg)


if typ.TYPE_CHECKING:
    from socketserver import ThreadingUnixStreamServer as _BaseUnixServer
    from types import TracebackType

    from .named_pipe import CallbackNamedPipeServer, NamedPipeServer
else:
    _BaseUnixServer = _resolve_unix_server_base()

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

    def _start_backend_thread(self, thread: threading.Thread) -> None:
        thread.start()

    def _wait_until_ready(self) -> None:  # ruff: ignore[empty-method-without-abstract-decorator] - base lifecycle hook intentionally has no default implementation
        """Wait for the backend server to be ready to accept connections."""

    def _stop_backend(self, server: BackendT | None) -> None:  # ruff: ignore[empty-method-without-abstract-decorator] - base lifecycle hook intentionally has no default implementation
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

    def _dispatch[DispatchArg: (Invocation, PassthroughResult)](
        self,
        handler: cabc.Callable[[DispatchArg], Response] | None,
        argument: DispatchArg,
        *,
        default: cabc.Callable[[DispatchArg], Response],
        error_builder: cabc.Callable[[DispatchArg, Exception], RuntimeError]
        | None = None,
    ) -> Response:
        """Invoke *handler* when provided, otherwise fall back to *default*."""  # ruff: ignore[docstring-missing-returns, docstring-missing-exception] - private dispatch helper has a clear response type and wraps callback failures locally
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
        """Echo the command name when no handler overrides the behaviour."""  # ruff: ignore[docstring-missing-returns] - private default handler has an obvious response return
        return Response(stdout=invocation.command)

    @staticmethod
    def _raise_unhandled_passthrough(result: PassthroughResult) -> Response:
        """Raise when passthrough results lack a configured handler."""  # ruff: ignore[docstring-missing-exception] - private default handler raises only for an unhandled internal dispatch
        msg = f"Unhandled passthrough result for {result.invocation_id}"
        raise RuntimeError(msg)

    @staticmethod
    def _build_passthrough_error(
        result: PassthroughResult, exc: Exception
    ) -> RuntimeError:
        """Create the wrapped passthrough error surfaced to callers."""  # ruff: ignore[docstring-missing-returns] - private error adapter has an obvious RuntimeError return
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


class IPCServer(_BaseIPCServer["_InnerServer"]):
    """Run a Unix domain socket server for shims."""

    def _prepare_backend_start(self) -> None:
        cleanup_stale_socket(self.socket_path)

    def _create_backend(self) -> tuple[_InnerServer, threading.Thread]:
        server = _InnerServer(self.socket_path, self)
        server.timeout = self.accept_timeout
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        return server, thread

    def _wait_until_ready(self) -> None:
        wait_for_socket(self.socket_path, self.timeout)

    def _stop_backend(self, server: _InnerServer | None) -> None:
        if server is None:
            return
        server.shutdown()
        server.server_close()

    def _post_stop_cleanup(self) -> None:
        if self.socket_path.exists():
            with contextlib.suppress(OSError):
                self.socket_path.unlink()


class CallbackIPCServer(IPCServer):
    """IPCServer variant that delegates to callbacks."""

    def __init__(
        self,
        socket_path: Path,
        handler: cabc.Callable[[Invocation], Response],
        passthrough_handler: cabc.Callable[[PassthroughResult], Response],
        *,
        timeouts: TimeoutConfig | None = None,
    ) -> None:
        """Initialise a callback-driven IPC server."""
        timeouts = timeouts or TimeoutConfig()
        super().__init__(
            socket_path,
            timeout=timeouts.timeout,
            accept_timeout=timeouts.accept_timeout,
            handlers=IPCHandlers(
                handler=handler,
                passthrough_handler=passthrough_handler,
            ),
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


@dc.dataclass(slots=True)
class ParsedRequest:
    """Parsed request containing payload and dispatch metadata."""

    payload: dict[str, typ.Any]
    kind: str
    validator: _RequestValidator
    processor: _RequestProcessor

    def validate(self) -> Invocation | PassthroughResult | None:
        """Run the validator associated with this request payload.

        Returns
        -------
        Invocation, PassthroughResult, or None
            The validated request object, or ``None`` for invalid payloads.
        """
        return self.validator(self.payload)


def _decode_payload(raw: bytes) -> dict[str, typ.Any] | None:
    """Decode raw request bytes into a mapping, logging malformed input once."""  # ruff: ignore[docstring-missing-returns] - private wire parser has an obvious optional mapping return
    payload = parse_json_safely(raw)
    if payload is not None:
        return payload

    try:
        _ = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        logger.error("IPC received malformed JSON")  # ruff: ignore[error-instead-of-exception] - logging exception details could expose untrusted bytes.
        return None

    logger.error("IPC payload is not a mapping")
    return None


def _parse_payload(raw: bytes) -> ParsedRequest | None:
    payload = _decode_payload(raw)
    if payload is None:
        return None

    kind = str(payload.get("kind", KIND_INVOCATION))
    handler_entry = _REQUEST_HANDLERS.get(kind)
    if handler_entry is None:
        logger.error("Unknown IPC payload kind")
        return None

    body = {key: value for key, value in payload.items() if key != "kind"}
    validator, processor = handler_entry
    return ParsedRequest(
        payload=body,
        kind=kind,
        validator=validator,
        processor=processor,
    )


def _encode_response(response: Response) -> bytes:
    return json.dumps(response.to_dict()).encode("utf-8")


def _emit_dispatch_outcome(
    kind: str,
    request: Invocation | PassthroughResult | None,
    outcome: _DispatchOutcome,
    *,
    error_category: str | None = None,
) -> None:
    """Emit bounded metadata for one IPC dispatch outcome.

    Parameters
    ----------
    kind : str
        Validated protocol request kind.
    request : Invocation or PassthroughResult or None
        Parsed request model, when validation succeeded.
    outcome : {"success", "invalid_request", "handler_error"}
        Terminal result of the shared dispatch pipeline.
    error_category : str or None, optional
        Stable failure category, when the outcome is not successful.
    """
    extra: dict[str, str] = {
        "operation": "ipc.dispatch",
        "kind": kind,
        "outcome": outcome,
    }
    if isinstance(request, PassthroughResult):
        extra["invocation_id"] = request.invocation_id
    if error_category is not None:
        extra["error_category"] = error_category
    logger.info("IPC dispatch outcome", extra=extra)


def _request_pipeline(server: _BaseIPCServer[typ.Any], raw: bytes) -> bytes | None:
    """Parse, validate, dispatch, and encode an IPC request in order."""  # ruff: ignore[docstring-missing-returns] - private wire pipeline has an obvious optional bytes return
    parsed = _parse_payload(raw)
    if parsed is None:
        return None

    obj = parsed.validate()
    if obj is None:
        _emit_dispatch_outcome(
            parsed.kind,
            None,
            "invalid_request",
            error_category="ValidationError",
        )
        return None

    response, error_category = _execute_request(server, parsed.processor, obj)
    outcome: _DispatchOutcome = "success"
    if error_category is not None:
        outcome = "handler_error"
    _emit_dispatch_outcome(
        parsed.kind,
        obj,
        outcome,
        error_category=error_category,
    )
    return _encode_response(response)


def _raise_invalid_request_dispatch(
    processor: _RequestProcessor, obj: Invocation | PassthroughResult
) -> typ.NoReturn:
    """Raise when the request registry maps a validator to the wrong hook."""  # ruff: ignore[docstring-missing-exception] - private registry guard raises only for an internal wiring invariant
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


class _IPCHandler(socketserver.StreamRequestHandler):
    """Handle a single shim connection."""

    def handle(self) -> None:  # pragma: no cover - exercised via behaviour tests
        raw = self.rfile.read()
        response_bytes = _request_pipeline(self.server.outer, raw)  # type: ignore[attr-defined, ty:unresolved-attribute]
        if response_bytes is None:
            return
        self.wfile.write(response_bytes)
        self.wfile.flush()


class _InnerServer(_BaseUnixServer):
    """Threaded Unix stream server passing requests to :class:`IPCServer`."""

    def __init__(self, socket_path: Path, outer: IPCServer) -> None:
        self.outer = outer
        super().__init__(str(socket_path), _IPCHandler)
        self.daemon_threads = True


def __getattr__(name: str) -> object:
    """Lazily expose named-pipe servers after defining the shared pipeline.

    Raises
    ------
    AttributeError
        If *name* is not a public named-pipe server.
    """  # ruff: ignore[docstring-missing-returns] - module attribute hook returns different public server classes
    if name not in {"CallbackNamedPipeServer", "NamedPipeServer"}:
        raise AttributeError(name)
    named_pipe = importlib.import_module(f"{__package__}.named_pipe")
    return getattr(named_pipe, name)


__all__ = [
    "CallbackIPCServer",
    "CallbackNamedPipeServer",
    "IPCHandlers",
    "IPCServer",
    "NamedPipeServer",
    "TimeoutConfig",
]
