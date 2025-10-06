"""Unix domain socket server for CmdMox shims."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import json
import logging
import socketserver
import threading
import typing as t
from pathlib import Path

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

if t.TYPE_CHECKING:
    from types import TracebackType

logger = logging.getLogger(__name__)

_RequestValidator = t.Callable[[dict[str, t.Any]], t.Any | None]
_DispatchArg = t.TypeVar("_DispatchArg", Invocation, PassthroughResult)


def _process_invocation(server: IPCServer, invocation: Invocation) -> Response:
    """Invoke :meth:`IPCServer.handle_invocation` for *invocation*."""
    return server.handle_invocation(invocation)


def _process_passthrough_result(
    server: IPCServer, result: PassthroughResult
) -> Response:
    """Invoke :meth:`IPCServer.handle_passthrough_result` for *result*."""
    return server.handle_passthrough_result(result)


@dc.dataclass(slots=True)
class IPCHandlers:
    """Optional callbacks customising :class:`IPCServer` behaviour."""

    handler: t.Callable[[Invocation], Response] | None = None
    passthrough_handler: t.Callable[[PassthroughResult], Response] | None = None


@dc.dataclass(slots=True)
class TimeoutConfig:
    """Timeout configuration forwarded by :class:`CallbackIPCServer`."""

    timeout: float = 5.0
    accept_timeout: float | None = None

    def __post_init__(self) -> None:
        """Validate timeout values to catch misconfiguration early."""
        validate_positive_finite_timeout(self.timeout)
        validate_optional_timeout(self.accept_timeout, name="accept_timeout")


class IPCServer:
    """Run a Unix domain socket server for shims.

    The server listens on a Unix domain socket created by
    :class:`~cmd_mox.environment.EnvironmentManager`. Clients connect via the
    ``CMOX_IPC_SOCKET`` path and communicate using JSON messages. Connection
    attempts default to a five second timeout, but this can be overridden by
    setting :data:`~cmd_mox.environment.CMOX_IPC_TIMEOUT_ENV` in the
    environment. See the ``IPC server`` section of the design document for
    details on the rationale and configuration:
    ``docs/python-native-command-mocking-design.md``.
    """

    def __init__(
        self,
        socket_path: Path,
        timeout: float = 5.0,
        accept_timeout: float | None = None,
        *,
        handlers: IPCHandlers | None = None,
    ) -> None:
        """Create a server listening at *socket_path*."""
        self.socket_path = Path(socket_path)
        validate_positive_finite_timeout(timeout)
        validate_optional_timeout(accept_timeout, name="accept_timeout")
        self.timeout = timeout
        self.accept_timeout = accept_timeout or min(0.1, timeout / 10)
        self._server: _InnerServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        handlers = handlers or IPCHandlers()
        self._handler = handlers.handler
        self._passthrough_handler = handlers.passthrough_handler

    def _dispatch(
        self,
        handler: t.Callable[[_DispatchArg], Response] | None,
        argument: _DispatchArg,
        *,
        default: t.Callable[[_DispatchArg], Response],
        error_builder: t.Callable[[_DispatchArg, Exception], RuntimeError]
        | None = None,
    ) -> Response:
        """Invoke *handler* when provided, otherwise fall back to *default*."""
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
        """Echo the command name when no handler overrides the behaviour."""
        return Response(stdout=invocation.command)

    @staticmethod
    def _raise_unhandled_passthrough(result: PassthroughResult) -> Response:
        """Raise when passthrough results lack a configured handler."""
        msg = f"Unhandled passthrough result for {result.invocation_id}"
        raise RuntimeError(msg)

    @staticmethod
    def _build_passthrough_error(
        result: PassthroughResult, exc: Exception
    ) -> RuntimeError:
        """Create the wrapped passthrough error surfaced to callers."""
        msg = f"Exception in passthrough handler for {result.invocation_id}: {exc}"
        return RuntimeError(msg)

    def __enter__(self) -> IPCServer:
        """Start the server when entering a context."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop the server when leaving a context."""
        self.stop()

    def start(self) -> None:
        """Start the background server thread."""
        with self._lock:
            if self._thread:
                msg = "IPC server already started"
                raise RuntimeError(msg)

            cleanup_stale_socket(self.socket_path)

            env_mgr = EnvironmentManager.get_active_manager()
            if env_mgr is not None:
                env_mgr.export_ipc_environment(timeout=self.timeout)

            server = _InnerServer(self.socket_path, self)
            server.timeout = self.accept_timeout
            thread = threading.Thread(target=server.serve_forever, daemon=True)

            self._server = server
            self._thread = thread

        thread.start()

        wait_for_socket(self.socket_path, self.timeout)

    def stop(self) -> None:
        """Stop the server and clean up the socket."""
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None

        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(self.timeout)
        if self.socket_path.exists():
            with contextlib.suppress(OSError):
                self.socket_path.unlink()

    def handle_invocation(self, invocation: Invocation) -> Response:
        """Process invocations using the configured handler when available."""
        return self._dispatch(
            self._handler,
            invocation,
            default=self._default_invocation_response,
        )

    def handle_passthrough_result(self, result: PassthroughResult) -> Response:
        """Handle passthrough results via the configured callback when provided."""
        return self._dispatch(
            self._passthrough_handler,
            result,
            default=self._raise_unhandled_passthrough,
            error_builder=self._build_passthrough_error,
        )


class CallbackIPCServer(IPCServer):
    """IPCServer variant that delegates to callbacks."""

    def __init__(
        self,
        socket_path: Path,
        handler: t.Callable[[Invocation], Response],
        passthrough_handler: t.Callable[[PassthroughResult], Response],
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


_RequestProcessor = t.Callable[[IPCServer, t.Any], Response]

_REQUEST_HANDLERS: dict[str, tuple[_RequestValidator, _RequestProcessor]] = {
    KIND_INVOCATION: (validate_invocation_payload, _process_invocation),
    KIND_PASSTHROUGH_RESULT: (
        validate_passthrough_payload,
        _process_passthrough_result,
    ),
}


class _IPCHandler(socketserver.StreamRequestHandler):
    """Handle a single shim connection."""

    def _parse_and_validate_request(
        self, raw: bytes
    ) -> tuple[dict[str, t.Any], str] | None:
        """Return a payload and kind when *raw* contains a valid request."""
        payload = parse_json_safely(raw)
        if payload is None:
            try:
                obj = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                logger.exception("IPC received malformed JSON")
                return None
            logger.error("IPC payload not a dict: %r", obj)
            return None

        copied_payload = payload.copy()
        kind = str(copied_payload.pop("kind", KIND_INVOCATION))
        return copied_payload, kind

    def _lookup_handler(
        self, kind: str
    ) -> tuple[_RequestValidator, _RequestProcessor] | None:
        """Return the registered handler for *kind* if available."""
        handler_entry = _REQUEST_HANDLERS.get(kind)
        if handler_entry is None:
            logger.error("Unknown IPC payload kind: %r", kind)
            return None
        return handler_entry

    def _process_request(self, processor: _RequestProcessor, obj: object) -> Response:
        """Execute *processor* and wrap unexpected failures."""
        try:
            return processor(self.server.outer, obj)  # type: ignore[attr-defined]
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.exception("IPC handler raised an exception")
            message = str(exc) or exc.__class__.__name__
            return Response(stderr=message, exit_code=1)

    def handle(self) -> None:  # pragma: no cover - exercised via behaviour tests
        raw = self.rfile.read()
        parsed = self._parse_and_validate_request(raw)
        if parsed is None:
            return

        payload, kind = parsed
        handler_entry = self._lookup_handler(kind)
        if handler_entry is None:
            return

        validator, processor = handler_entry
        obj = validator(payload)
        if obj is None:
            return

        response = self._process_request(processor, obj)
        self.wfile.write(json.dumps(response.to_dict()).encode("utf-8"))
        self.wfile.flush()


class _InnerServer(socketserver.ThreadingUnixStreamServer):
    """Threaded Unix stream server passing requests to :class:`IPCServer`."""

    def __init__(self, socket_path: Path, outer: IPCServer) -> None:
        self.outer = outer
        super().__init__(str(socket_path), _IPCHandler)
        self.daemon_threads = True


__all__ = [
    "CallbackIPCServer",
    "IPCHandlers",
    "IPCServer",
    "TimeoutConfig",
]
