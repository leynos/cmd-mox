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
from cmd_mox.environment import IS_WINDOWS, EnvironmentManager

from . import win32 as win32ipc
from .constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from .json_utils import (
    parse_json_safely,
    validate_invocation_payload,
    validate_passthrough_payload,
)
from .models import Invocation, PassthroughResult, Response
from .socket_utils import cleanup_stale_socket, wait_for_socket

if t.TYPE_CHECKING:
    from socketserver import ThreadingUnixStreamServer as _TypingUnixServer
    from types import TracebackType

    _BaseUnixServer = _TypingUnixServer
else:
    _BaseUnixServer: type[socketserver.BaseServer] | None
    if hasattr(socketserver, "ThreadingUnixStreamServer"):
        _BaseUnixServer = socketserver.ThreadingUnixStreamServer
    elif hasattr(socketserver, "UnixStreamServer"):

        class _ThreadingUnixStreamServerCompat(
            socketserver.ThreadingMixIn,  # type: ignore[misc]
            socketserver.UnixStreamServer,  # type: ignore[attr-defined]
        ):
            """Compatibility shim for platforms lacking ThreadingUnixStreamServer."""

            pass

        _BaseUnixServer = _ThreadingUnixStreamServerCompat
    else:  # pragma: no cover - exercised on unsupported platforms only
        _BaseUnixServer = None

_UNSUPPORTED_UNIX_SOCKET_MESSAGE = (
    "Unix domain socket servers are not supported on this platform"
)

_UNIX_SOCKET_SERVER_SUPPORTED = _BaseUnixServer is not None


def _ensure_unix_socket_support() -> None:
    """Raise a descriptive error when Unix-domain sockets are unavailable."""
    if not _UNIX_SOCKET_SERVER_SUPPORTED:  # pragma: no cover - platform guard
        raise RuntimeError(_UNSUPPORTED_UNIX_SOCKET_MESSAGE)


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
        _ensure_unix_socket_support()
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


_RequestProcessor = t.Callable[[IPCServer, t.Any], Response]

_REQUEST_HANDLERS: dict[str, tuple[_RequestValidator, _RequestProcessor]] = {
    KIND_INVOCATION: (validate_invocation_payload, _process_invocation),
    KIND_PASSTHROUGH_RESULT: (
        validate_passthrough_payload,
        _process_passthrough_result,
    ),
}


def _execute_request(
    server: IPCServer, processor: _RequestProcessor, obj: object
) -> Response:
    """Execute *processor* and wrap unexpected failures."""
    try:
        return processor(server, obj)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:
        logger.exception("IPC handler raised an exception")
        message = str(exc) or exc.__class__.__name__
        return Response(stderr=message, exit_code=1)


def _handle_raw_request(server: IPCServer, raw: bytes) -> bytes | None:
    """Process a raw request payload and return the encoded response."""
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
    handler_entry = _REQUEST_HANDLERS.get(kind)
    if handler_entry is None:
        logger.error("Unknown IPC payload kind: %r", kind)
        return None

    validator, processor = handler_entry
    obj = validator(copied_payload)
    if obj is None:
        return None

    response = _execute_request(server, processor, obj)
    return json.dumps(response.to_dict()).encode("utf-8")


class _IPCHandler(socketserver.StreamRequestHandler):
    """Handle a single shim connection."""

    def handle(self) -> None:  # pragma: no cover - exercised via behaviour tests
        raw = self.rfile.read()
        response = _handle_raw_request(self.server.outer, raw)  # type: ignore[attr-defined]
        if response is None:
            return
        self.wfile.write(response)
        self.wfile.flush()


class NamedPipeServer(IPCServer):
    """Windows named pipe implementation mirroring :class:`IPCServer`."""

    def __init__(
        self,
        socket_path: Path,
        timeout: float = 5.0,
        *,
        handlers: IPCHandlers | None = None,
    ) -> None:
        super().__init__(socket_path, timeout=timeout, handlers=handlers)
        self._stop_event: threading.Event | None = None
        self._client_threads: set[threading.Thread] = set()
        self._pipe_api: win32ipc.NamedPipeAPI | None = None

    def start(self) -> None:
        """Start listening for named pipe connections."""
        api = win32ipc.require_named_pipe_api("NamedPipeServer")
        with self._lock:
            if self._thread:
                msg = "IPC server already started"
                raise RuntimeError(msg)

            env_mgr = EnvironmentManager.get_active_manager()
            if env_mgr is not None:
                env_mgr.export_ipc_environment(timeout=self.timeout)

            stop_event = threading.Event()
            thread = threading.Thread(
                target=self._serve_forever,
                args=(stop_event,),
                daemon=True,
            )

            self._thread = thread
            self._stop_event = stop_event
            self._pipe_api = api

        thread.start()

    def stop(self) -> None:
        """Stop the server and join worker threads."""
        with self._lock:
            thread = self._thread
            stop_event = self._stop_event
            client_threads = list(self._client_threads)
            self._thread = None
            self._stop_event = None
            self._client_threads.clear()

        if stop_event is not None:
            stop_event.set()
            self._poke_pipe()

        if thread is not None:
            thread.join(self.timeout)

        for worker in client_threads:
            worker.join(self.timeout)

        self._pipe_api = None

    def _serve_forever(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            handle = self._create_pipe_instance()
            if handle is None:
                return
            if not self._connect_client(handle, stop_event):
                self._close_handle(handle)
                continue
            worker = threading.Thread(
                target=self._handle_connection,
                args=(handle,),
                daemon=True,
            )
            with self._lock:
                if self._stop_event is stop_event:
                    self._client_threads.add(worker)
                else:  # server stopped while waiting for lock
                    self._close_handle(handle)
                    return
            worker.start()

    def _create_pipe_instance(self) -> object | None:
        api = self._pipe_api
        if api is None:
            return None
        return api.create_named_pipe(str(self.socket_path), self.timeout)

    def _connect_client(self, handle: object, stop_event: threading.Event) -> bool:
        api = self._pipe_api
        if api is None:
            return False
        try:
            api.connect_named_pipe(handle)
        except api.pywintypes.error as exc:  # type: ignore[union-attr]
            if exc.winerror == api.win32pipe.ERROR_PIPE_CONNECTED:
                pass
            elif stop_event.is_set():
                return False
            else:
                logger.debug("Named pipe connection failed: %s", exc)
                return False
        return not stop_event.is_set()

    def _handle_connection(self, handle: object) -> None:
        worker = threading.current_thread()
        try:
            if self._stop_event and self._stop_event.is_set():
                return
            self._configure_pipe(handle)
            raw = self._read_from_pipe(handle)
            if not raw:
                return
            response = _handle_raw_request(self, raw)
            if response is None:
                return
            self._write_to_pipe(handle, response)
        finally:
            self._close_handle(handle)
            with self._lock:
                self._client_threads.discard(worker)

    def _configure_pipe(self, handle: object) -> None:
        api = self._pipe_api
        if api is None:
            return
        try:
            api.set_message_mode(handle)
        except api.pywintypes.error:  # type: ignore[union-attr]
            logger.debug("Failed to adjust named pipe read mode", exc_info=True)

    def _read_from_pipe(self, handle: object) -> bytes:
        api = self._pipe_api
        if api is None:
            return b""
        return api.read_from_pipe(handle)

    def _write_to_pipe(self, handle: object, payload: bytes) -> None:
        api = self._pipe_api
        if api is None:
            return
        api.write_to_pipe(handle, payload)

    def _close_handle(self, handle: object) -> None:
        api = self._pipe_api
        if api is None:
            return
        api.disconnect_named_pipe(handle)
        api.close_handle(handle)

    def _poke_pipe(self) -> None:
        api = self._pipe_api
        if api is None:
            return
        api.poke_server(str(self.socket_path))


class _UnixCallbackIPCServer(IPCServer):
    """Unix-domain callback server implementation."""

    def __init__(
        self,
        socket_path: Path,
        handler: t.Callable[[Invocation], Response],
        passthrough_handler: t.Callable[[PassthroughResult], Response],
        *,
        timeouts: TimeoutConfig | None = None,
    ) -> None:
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


class _WindowsCallbackIPCServer(NamedPipeServer):
    """Named pipe callback server implementation."""

    def __init__(
        self,
        socket_path: Path,
        handler: t.Callable[[Invocation], Response],
        passthrough_handler: t.Callable[[PassthroughResult], Response],
        *,
        timeouts: TimeoutConfig | None = None,
    ) -> None:
        timeouts = timeouts or TimeoutConfig()
        super().__init__(
            socket_path,
            timeout=timeouts.timeout,
            handlers=IPCHandlers(
                handler=handler,
                passthrough_handler=passthrough_handler,
            ),
        )


CallbackIPCServer = _WindowsCallbackIPCServer if IS_WINDOWS else _UnixCallbackIPCServer


if _UNIX_SOCKET_SERVER_SUPPORTED:

    class _InnerServer(_BaseUnixServer):  # type: ignore[misc]
        """Threaded Unix stream server passing requests to :class:`IPCServer`."""

        def __init__(self, socket_path: Path, outer: IPCServer) -> None:
            self.outer = outer
            super().__init__(str(socket_path), _IPCHandler)
            self.daemon_threads = True

else:  # pragma: no cover - exercised only on platforms without Unix sockets

    class _InnerServer:  # type: ignore[too-many-ancestors]
        """Placeholder that raises when Unix sockets are unsupported."""

        def __init__(self, *_: object, **__: object) -> None:
            _ensure_unix_socket_support()


__all__ = [
    "CallbackIPCServer",
    "IPCHandlers",
    "IPCServer",
    "NamedPipeServer",
    "TimeoutConfig",
]
