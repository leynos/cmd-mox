"""IPC servers for CmdMox shims (Unix sockets and Windows named pipes)."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import importlib
import json
import logging
import os
import socketserver
import threading
import time
import typing as t
from pathlib import Path
from types import TracebackType

from cmd_mox._validators import (
    validate_optional_timeout,
    validate_positive_finite_timeout,
)
from cmd_mox.environment import EnvironmentManager
from cmd_mox.ipc.windows import (
    ERROR_BROKEN_PIPE,
    ERROR_FILE_NOT_FOUND,
    ERROR_MORE_DATA,
    ERROR_NO_DATA,
    ERROR_OPERATION_ABORTED,
    ERROR_PIPE_BUSY,
    ERROR_PIPE_CONNECTED,
    PIPE_CHUNK_SIZE,
    derive_pipe_name,
)

from .constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from .json_utils import (
    parse_json_safely,
    validate_invocation_payload,
    validate_passthrough_payload,
)
from .models import Invocation, PassthroughResult, Response
from .socket_utils import cleanup_stale_socket, wait_for_socket

IS_WINDOWS = os.name == "nt"


def _resolve_unix_server_base() -> type[socketserver.BaseServer]:
    if hasattr(socketserver, "ThreadingUnixStreamServer"):
        return t.cast(
            type[socketserver.BaseServer], socketserver.ThreadingUnixStreamServer
        )
    if hasattr(socketserver, "UnixStreamServer"):

        class _ThreadingUnixStreamServerCompat(
            socketserver.ThreadingMixIn,  # type: ignore[misc]
            socketserver.UnixStreamServer,  # type: ignore[attr-defined]
        ):
            """Compatibility shim for platforms lacking ThreadingUnixStreamServer."""

            pass

        return t.cast(
            type[socketserver.BaseServer], _ThreadingUnixStreamServerCompat
        )
    if IS_WINDOWS:

        class _UnsupportedUnixServer(socketserver.BaseServer):  # type: ignore[misc]
            """Placeholder that raises when Unix sockets are requested on Windows."""

            def __init__(self, *args: object, **kwargs: object) -> None:
                msg = "Unix domain socket servers are unavailable on Windows"
                raise RuntimeError(msg)

        return t.cast(type[socketserver.BaseServer], _UnsupportedUnixServer)
    msg = "Unix domain socket servers are not supported on this platform"
    raise RuntimeError(msg)
if t.TYPE_CHECKING:
    _BaseUnixServer = socketserver.ThreadingUnixStreamServer
else:
    _BaseUnixServer = _resolve_unix_server_base()

if IS_WINDOWS:  # pragma: win32-only
    try:
        pywintypes = importlib.import_module("pywintypes")
        win32file = importlib.import_module("win32file")
        win32pipe = importlib.import_module("win32pipe")
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        msg = "pywin32 is required for Windows named pipe support"
        raise RuntimeError(msg) from exc
else:  # pragma: no cover - non-Windows fallback for type-checkers
    pywintypes = t.cast("t.Any", None)
    win32file = t.cast("t.Any", None)
    win32pipe = t.cast("t.Any", None)

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
        self._server: object | None = None
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

    def _prepare_backend_start(self) -> None:
        cleanup_stale_socket(self.socket_path)

    def _export_environment(self) -> None:
        env_mgr = EnvironmentManager.get_active_manager()
        if env_mgr is not None:
            env_mgr.export_ipc_environment(timeout=self.timeout)

    def _create_backend(self) -> tuple[object, threading.Thread]:
        server = _InnerServer(self.socket_path, self)
        server.timeout = self.accept_timeout
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        return server, thread

    def _start_backend_thread(self, thread: threading.Thread) -> None:
        thread.start()

    def _wait_until_ready(self) -> None:
        wait_for_socket(self.socket_path, self.timeout)

    def _stop_backend(self, server: object | None) -> None:
        if server is None:
            return
        unix_server = t.cast("_InnerServer", server)
        unix_server.shutdown()
        unix_server.server_close()

    def _join_backend_thread(self, thread: threading.Thread | None) -> None:
        if thread is None:
            return
        thread.join(self.timeout)

    def _post_stop_cleanup(self) -> None:
        if self.socket_path.exists():
            with contextlib.suppress(OSError):
                self.socket_path.unlink()

    def start(self) -> None:
        """Start the background server thread."""
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
        """Stop the server and clean up the socket."""
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None

        self._stop_backend(server)
        self._join_backend_thread(thread)
        self._post_stop_cleanup()

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


def _parse_payload(raw: bytes) -> tuple[dict[str, t.Any], str] | None:
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


def _lookup_handler(kind: str) -> tuple[_RequestValidator, _RequestProcessor] | None:
    handler_entry = _REQUEST_HANDLERS.get(kind)
    if handler_entry is None:
        logger.error("Unknown IPC payload kind: %r", kind)
        return None
    return handler_entry


def _process_raw_request(server: IPCServer, raw: bytes) -> bytes | None:
    parsed = _parse_payload(raw)
    if parsed is None:
        return None

    payload, kind = parsed
    handler_entry = _lookup_handler(kind)
    if handler_entry is None:
        return None

    validator, processor = handler_entry
    obj = validator(payload)
    if obj is None:
        return None

    response = _execute_request(server, processor, obj)
    return json.dumps(response.to_dict()).encode("utf-8")


def _execute_request(
    server: IPCServer, processor: _RequestProcessor, obj: object
) -> Response:
    try:
        return processor(server, obj)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("IPC handler raised an exception")
        message = str(exc) or exc.__class__.__name__
        return Response(stderr=message, exit_code=1)


class _IPCHandler(socketserver.StreamRequestHandler):
    """Handle a single shim connection."""

    def handle(self) -> None:  # pragma: no cover - exercised via behaviour tests
        raw = self.rfile.read()
        response_bytes = _process_raw_request(self.server.outer, raw)  # type: ignore[attr-defined]
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


class NamedPipeServer(IPCServer):
    """Windows named pipe variant of :class:`IPCServer`."""

    def __init__(
        self,
        socket_path: Path,
        timeout: float = 5.0,
        accept_timeout: float | None = None,
        *,
        handlers: IPCHandlers | None = None,
    ) -> None:
        if not IS_WINDOWS:
            msg = "NamedPipeServer is only available on Windows"
            raise RuntimeError(msg)
        super().__init__(
            socket_path,
            timeout=timeout,
            accept_timeout=accept_timeout,
            handlers=handlers,
        )
        self._pipe_name = derive_pipe_name(self.socket_path)

    def _prepare_backend_start(self) -> None:
        # Named pipes do not leave filesystem artefacts that require cleanup.
        return

    def _create_backend(self) -> tuple[object, threading.Thread]:  # type: ignore[override]
        state = _NamedPipeState(
            pipe_name=self._pipe_name,
            outer=self,
            accept_timeout=self.accept_timeout,
        )
        thread = threading.Thread(target=state.serve_forever, daemon=True)
        return state, thread

    def _wait_until_ready(self) -> None:  # type: ignore[override]
        state = t.cast("_NamedPipeState | None", self._server)
        if state is None:
            return
        if not state.ready_event.wait(self.timeout):
            state.stop()
            msg = (
                f"Named pipe {self._pipe_name} not accepting connections within timeout"
            )
            raise RuntimeError(msg)

    def _stop_backend(self, server: object | None) -> None:  # type: ignore[override]
        if server is None:
            return
        state = t.cast("_NamedPipeState", server)
        state.stop()
        state.join_clients(self.timeout)


class CallbackNamedPipeServer(NamedPipeServer):
    """Callback-based helper mirroring :class:`CallbackIPCServer`."""

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


class _NamedPipeState:
    """Stateful helper managing named pipe connections and worker threads."""

    def __init__(
        self,
        *,
        pipe_name: str,
        outer: IPCServer,
        accept_timeout: float,
    ) -> None:
        self.pipe_name = pipe_name
        self.outer = outer
        self.accept_timeout = accept_timeout
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self._client_threads: set[threading.Thread] = set()
        self._client_lock = threading.Lock()

    def serve_forever(self) -> None:
        if not IS_WINDOWS:  # pragma: no cover - defensive guard
            return

        while not self.stop_event.is_set():
            handle = self._create_pipe_instance()
            if not self.ready_event.is_set():
                self.ready_event.set()
            try:
                win32pipe.ConnectNamedPipe(handle, None)  # type: ignore[union-attr]
            except pywintypes.error as exc:  # type: ignore[name-defined]
                if exc.winerror == ERROR_PIPE_CONNECTED:
                    pass
                elif exc.winerror in (ERROR_OPERATION_ABORTED, ERROR_NO_DATA):
                    win32file.CloseHandle(handle)  # type: ignore[union-attr]
                    break
                else:
                    logger.exception("Named pipe connect failed")
                    win32file.CloseHandle(handle)  # type: ignore[union-attr]
                    continue

            if self.stop_event.is_set():
                win32file.CloseHandle(handle)  # type: ignore[union-attr]
                break

            thread = threading.Thread(
                target=self._handle_client,
                args=(handle,),
                daemon=True,
            )
            with self._client_lock:
                self._client_threads.add(thread)
            thread.start()

    def stop(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.ready_event.set()
        self._poke_pipe()

    def join_clients(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            with self._client_lock:
                threads = list(self._client_threads)
            if not threads:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            for thread in threads:
                thread.join(max(0.0, remaining))

    def _create_pipe_instance(self) -> object:
        timeout_ms = max(1, int(self.accept_timeout * 1000))
        return win32pipe.CreateNamedPipe(  # type: ignore[union-attr]
            self.pipe_name,
            win32pipe.PIPE_ACCESS_DUPLEX,  # type: ignore[union-attr]
            win32pipe.PIPE_TYPE_MESSAGE  # type: ignore[union-attr]
            | win32pipe.PIPE_READMODE_MESSAGE  # type: ignore[union-attr]
            | win32pipe.PIPE_WAIT,  # type: ignore[union-attr]
            win32pipe.PIPE_UNLIMITED_INSTANCES,  # type: ignore[union-attr]
            PIPE_CHUNK_SIZE,
            PIPE_CHUNK_SIZE,
            timeout_ms,
            None,
        )

    def _handle_client(self, handle: object) -> None:
        thread = threading.current_thread()
        try:
            raw = self._read_request(handle)
            if raw is None:
                return
            response_bytes = _process_raw_request(self.outer, raw)
            if response_bytes is not None:
                win32file.WriteFile(handle, response_bytes)  # type: ignore[union-attr]
                win32file.FlushFileBuffers(handle)  # type: ignore[union-attr]
        except pywintypes.error as exc:  # type: ignore[name-defined]
            if exc.winerror not in (ERROR_BROKEN_PIPE, ERROR_NO_DATA):
                logger.exception("Named pipe handler failed")
        finally:
            with contextlib.suppress(pywintypes.error):  # type: ignore[name-defined]
                win32pipe.DisconnectNamedPipe(handle)  # type: ignore[union-attr]
            win32file.CloseHandle(handle)  # type: ignore[union-attr]
            with self._client_lock:
                self._client_threads.discard(thread)

    def _read_request(self, handle: object) -> bytes | None:
        chunks: list[bytes] = []
        while True:
            try:
                hr, data = win32file.ReadFile(handle, PIPE_CHUNK_SIZE)  # type: ignore[union-attr]
            except pywintypes.error as exc:  # type: ignore[name-defined]
                if exc.winerror == ERROR_BROKEN_PIPE:
                    break
                raise
            chunks.append(data)
            if hr == 0:
                break
            if hr != ERROR_MORE_DATA:
                break
        return b"".join(chunks)

    def _poke_pipe(self) -> None:
        try:
            handle = win32file.CreateFile(  # type: ignore[union-attr]
                self.pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,  # type: ignore[union-attr]
                0,
                None,
                win32file.OPEN_EXISTING,  # type: ignore[union-attr]
                0,
                None,
            )
        except pywintypes.error as exc:  # type: ignore[name-defined]
            if exc.winerror not in (ERROR_PIPE_BUSY, ERROR_FILE_NOT_FOUND):
                logger.debug("Named pipe wakeup failed: %s", exc)
            return
        else:
            win32file.CloseHandle(handle)  # type: ignore[union-attr]


__all__ = [
    "CallbackIPCServer",
    "CallbackNamedPipeServer",
    "IPCHandlers",
    "IPCServer",
    "NamedPipeServer",
    "TimeoutConfig",
]
