"""IPC server implementations for CmdMox shims."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import json
import logging
import os
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

IS_WINDOWS = os.name == "nt"

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from socketserver import ThreadingUnixStreamServer as _BaseUnixServer
    from types import TracebackType
else:  # pragma: no cover - compatibility for environments without the class
    if hasattr(socketserver, "ThreadingUnixStreamServer"):
        _BaseUnixServer = socketserver.ThreadingUnixStreamServer  # type: ignore[assignment]
    elif hasattr(socketserver, "UnixStreamServer"):

        class _ThreadingUnixStreamServerCompat(
            socketserver.ThreadingMixIn,  # type: ignore[misc]
            socketserver.UnixStreamServer,  # type: ignore[attr-defined]
        ):
            """Compatibility shim for platforms lacking ThreadingUnixStreamServer."""

            daemon_threads = True

        _BaseUnixServer = _ThreadingUnixStreamServerCompat  # type: ignore[assignment]
    else:
        if IS_WINDOWS:

            class _NoopUnixServer(socketserver.BaseServer):
                """Placeholder server used only to satisfy type checkers on Windows."""

                def __init__(
                    self, *_args: object, **_kwargs: object
                ) -> None:  # pragma: no cover - never instantiated
                    msg = "Unix domain sockets are unavailable on Windows"
                    raise RuntimeError(msg)

            _BaseUnixServer = _NoopUnixServer  # type: ignore[assignment]
        else:
            msg = "Unix domain socket servers are not supported on this platform"
            raise RuntimeError(msg)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - pywin32 only available on Windows
    if IS_WINDOWS:
        import pywintypes  # type: ignore[attr-defined]
        import win32file  # type: ignore[attr-defined]
        import win32pipe  # type: ignore[attr-defined]
        import winerror  # type: ignore[attr-defined]
    else:  # pragma: no cover - non-Windows platforms never import pywin32
        pywintypes = None  # type: ignore[assignment]
        win32file = None  # type: ignore[assignment]
        win32pipe = None  # type: ignore[assignment]
        winerror = None  # type: ignore[assignment]
except ModuleNotFoundError:  # pragma: no cover - handled at runtime
    pywintypes = None  # type: ignore[assignment]
    win32file = None  # type: ignore[assignment]
    win32pipe = None  # type: ignore[assignment]
    winerror = None  # type: ignore[assignment]

_HAS_WINDOWS_PIPES = (
    pywintypes is not None
    and win32file is not None
    and win32pipe is not None
    and winerror is not None
)
_WINDOWS_PIPE_ERROR: t.Final[str] = (
    "pywin32 is required for NamedPipeServer on Windows. Install it with "
    "'pip install pywin32' to enable Windows IPC support."
)

if t.TYPE_CHECKING:  # pragma: no cover
    PipeHandle = object
else:  # pragma: no cover - runtime fallback
    PipeHandle = object


def _require_windows_modules() -> tuple[t.Any, t.Any, t.Any, t.Any]:
    """Return pywin32 modules when available, otherwise raise an error."""
    if (
        not _HAS_WINDOWS_PIPES
        or pywintypes is None
        or win32file is None
        or win32pipe is None
        or winerror is None
    ):
        raise RuntimeError(_WINDOWS_PIPE_ERROR)
    return (
        t.cast("t.Any", pywintypes),
        t.cast("t.Any", win32file),
        t.cast("t.Any", win32pipe),
        t.cast("t.Any", winerror),
    )


_RequestValidator = t.Callable[[dict[str, t.Any]], t.Any | None]
_DispatchArg = t.TypeVar("_DispatchArg", Invocation, PassthroughResult)


def _process_invocation(server: BaseIPCServer, invocation: Invocation) -> Response:
    """Invoke :meth:`BaseIPCServer.handle_invocation` for *invocation*."""
    return server.handle_invocation(invocation)


def _process_passthrough_result(
    server: BaseIPCServer, result: PassthroughResult
) -> Response:
    """Invoke :meth:`BaseIPCServer.handle_passthrough_result` for *result*."""
    return server.handle_passthrough_result(result)


@dc.dataclass(slots=True)
class IPCHandlers:
    """Optional callbacks customising IPC server behaviour."""

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


class BaseIPCServer:
    """Common functionality shared by Unix sockets and Windows named pipes."""

    def __init__(
        self,
        socket_path: Path,
        timeout: float = 5.0,
        accept_timeout: float | None = None,
        *,
        handlers: IPCHandlers | None = None,
    ) -> None:
        self.socket_path = Path(socket_path)
        validate_positive_finite_timeout(timeout)
        validate_optional_timeout(accept_timeout, name="accept_timeout")
        self.timeout = timeout
        self.accept_timeout = accept_timeout or min(0.1, timeout / 10)
        self._lock = threading.Lock()
        self._resources: object | None = None
        handlers = handlers or IPCHandlers()
        self._handler = handlers.handler
        self._passthrough_handler = handlers.passthrough_handler

    def __enter__(self) -> BaseIPCServer:
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
        """Start the underlying IPC transport."""
        with self._lock:
            if self._resources is not None:
                msg = "IPC server already started"
                raise RuntimeError(msg)

            self._before_start()
            env_mgr = EnvironmentManager.get_active_manager()
            if env_mgr is not None:
                env_mgr.export_ipc_environment(timeout=self.timeout)

            resources = self._start_transport()
            self._resources = resources

        self._after_start(resources)

    def stop(self) -> None:
        """Stop the IPC transport and release server resources."""
        with self._lock:
            resources = self._resources
            self._resources = None

        if resources is None:
            return

        self._stop_transport(resources)

    def _before_start(self) -> None:
        """Perform subclass-specific pre-start housekeeping."""

    def _after_start(self, _resources: object) -> None:
        """Synchronise with transport startup for subclasses."""

    def _start_transport(self) -> object:
        raise NotImplementedError

    def _stop_transport(self, resources: object) -> None:
        raise NotImplementedError

    def _dispatch(
        self,
        handler: t.Callable[[t.Any], Response] | None,
        argument: _DispatchArg,
        *,
        default: t.Callable[[_DispatchArg], Response],
        error_builder: t.Callable[[_DispatchArg, Exception], RuntimeError]
        | None = None,
    ) -> Response:
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
        return Response(stdout=invocation.command)

    @staticmethod
    def _raise_unhandled_passthrough(result: PassthroughResult) -> Response:
        msg = f"Unhandled passthrough result for {result.invocation_id}"
        raise RuntimeError(msg)

    @staticmethod
    def _build_passthrough_error(
        result: PassthroughResult, exc: Exception
    ) -> RuntimeError:
        msg = f"Exception in passthrough handler for {result.invocation_id}: {exc}"
        return RuntimeError(msg)

    def handle_invocation(self, invocation: Invocation) -> Response:
        return self._dispatch(
            self._handler,
            invocation,
            default=self._default_invocation_response,
        )

    def handle_passthrough_result(self, result: PassthroughResult) -> Response:
        return self._dispatch(
            self._passthrough_handler,
            result,
            default=self._raise_unhandled_passthrough,
            error_builder=self._build_passthrough_error,
        )


class IPCServer(BaseIPCServer):
    """Run a Unix domain socket server for shims."""

    def _before_start(self) -> None:
        cleanup_stale_socket(self.socket_path)

    def _start_transport(self) -> tuple[_InnerServer, threading.Thread]:
        server = _InnerServer(self.socket_path, self)
        server.timeout = self.accept_timeout
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread

    def _after_start(self, _resources: object) -> None:
        wait_for_socket(self.socket_path, self.timeout)

    def _stop_transport(self, resources: object) -> None:
        server, thread = t.cast("tuple[_InnerServer, threading.Thread]", resources)
        server.shutdown()
        server.server_close()
        thread.join(self.timeout)
        if self.socket_path.exists():
            with contextlib.suppress(OSError):
                self.socket_path.unlink()


class NamedPipeServer(BaseIPCServer):
    """Windows named pipe server implemented via pywin32."""

    _BUFFER_SIZE: t.Final[int] = 64 * 1024
    _PIPE_PREFIX: t.Final[str] = r"\\.\\pipe\\"

    def __init__(
        self,
        socket_path: Path,
        timeout: float = 5.0,
        accept_timeout: float | None = None,
        *,
        handlers: IPCHandlers | None = None,
    ) -> None:
        if not _HAS_WINDOWS_PIPES:  # pragma: no cover - exercised on Windows
            raise RuntimeError(_WINDOWS_PIPE_ERROR)
        super().__init__(
            socket_path,
            timeout=timeout,
            accept_timeout=accept_timeout,
            handlers=handlers,
        )
        self._pipe_name = self._normalise_pipe_name(str(self.socket_path))
        self._shutdown = threading.Event()
        self._listener: threading.Thread | None = None
        self._workers: set[threading.Thread] = set()
        self._workers_lock = threading.Lock()

    def _before_start(self) -> None:  # pragma: no cover - trivial
        self._shutdown.clear()

    def _start_transport(self) -> threading.Thread:
        listener = threading.Thread(target=self._serve_forever, daemon=True)
        self._listener = listener
        listener.start()
        return listener

    def _stop_transport(self, resources: object) -> None:
        listener = t.cast("threading.Thread", resources)
        self._shutdown.set()
        self._poke_listener()
        listener.join(self.timeout)
        self._listener = None
        self._join_workers()

    def _normalise_pipe_name(self, raw: str) -> str:
        if raw.startswith(self._PIPE_PREFIX):
            return raw
        sanitized = raw.replace("/", "_").replace("\\", "_")
        return f"{self._PIPE_PREFIX}cmdmox-{sanitized}"

    def _serve_forever(self) -> None:
        pywintypes_mod, win32file_mod, _, _ = _require_windows_modules()
        while not self._shutdown.is_set():
            try:
                handle = self._create_pipe_instance()
            except pywintypes_mod.error:  # type: ignore[attr-defined]
                logger.exception("Failed to create named pipe instance")
                return
            try:
                if not self._await_client(handle):
                    win32file_mod.CloseHandle(handle)
                    continue
            except pywintypes_mod.error:  # type: ignore[attr-defined]
                logger.exception("Error waiting for named pipe client")
                win32file_mod.CloseHandle(handle)
                continue
            worker = threading.Thread(
                target=self._handle_client,
                args=(handle,),
                daemon=True,
            )
            self._register_worker(worker)
            worker.start()

    def _create_pipe_instance(self) -> PipeHandle:
        _, _, win32pipe_mod, _ = _require_windows_modules()
        open_mode = win32pipe_mod.PIPE_ACCESS_DUPLEX
        pipe_mode = (
            win32pipe_mod.PIPE_TYPE_MESSAGE
            | win32pipe_mod.PIPE_READMODE_MESSAGE
            | win32pipe_mod.PIPE_WAIT
        )
        return win32pipe_mod.CreateNamedPipe(
            self._pipe_name,
            open_mode,
            pipe_mode,
            win32pipe_mod.PIPE_UNLIMITED_INSTANCES,
            self._BUFFER_SIZE,
            self._BUFFER_SIZE,
            int(self.accept_timeout * 1000),
            None,
        )

    def _await_client(self, handle: PipeHandle) -> bool:
        pywintypes_mod, _, win32pipe_mod, winerror_mod = _require_windows_modules()
        try:
            win32pipe_mod.ConnectNamedPipe(handle, None)
        except pywintypes_mod.error as exc:  # type: ignore[attr-defined]
            if exc.winerror == winerror_mod.ERROR_PIPE_CONNECTED:
                return True
            if exc.winerror in {
                winerror_mod.ERROR_OPERATION_ABORTED,
                winerror_mod.ERROR_NO_DATA,
            }:
                return False
            raise
        return True

    def _handle_client(self, handle: PipeHandle) -> None:
        pywintypes_mod, win32file_mod, win32pipe_mod, _ = _require_windows_modules()
        thread = threading.current_thread()
        try:
            raw = self._read_from_pipe(handle)
            if not raw:
                return
            response = _handle_raw_request(self, raw)
            if response is not None:
                self._write_response(handle, response)
        except pywintypes_mod.error:  # type: ignore[attr-defined]
            logger.exception("Named pipe client handler failed")
        finally:
            with contextlib.suppress(Exception):
                win32pipe_mod.DisconnectNamedPipe(handle)
            win32file_mod.CloseHandle(handle)
            self._discard_worker(thread)

    def _read_from_pipe(self, handle: PipeHandle) -> bytes:
        pywintypes_mod, win32file_mod, _, winerror_mod = _require_windows_modules()
        chunks: list[bytes] = []
        while True:
            hr, data = win32file_mod.ReadFile(handle, self._BUFFER_SIZE)
            chunks.append(data)
            if hr == 0:
                break
            if hr == winerror_mod.ERROR_MORE_DATA:
                continue
            raise pywintypes_mod.error(hr, "ReadFile", "Named pipe read failed")  # type: ignore[arg-type]
        return b"".join(chunks)

    def _write_response(self, handle: PipeHandle, response: Response) -> None:
        _, win32file_mod, _, _ = _require_windows_modules()
        payload = json.dumps(response.to_dict()).encode("utf-8")
        win32file_mod.WriteFile(handle, payload)
        win32file_mod.FlushFileBuffers(handle)

    def _register_worker(self, worker: threading.Thread) -> None:
        with self._workers_lock:
            self._workers.add(worker)

    def _discard_worker(self, worker: threading.Thread) -> None:
        with self._workers_lock:
            self._workers.discard(worker)

    def _join_workers(self) -> None:
        with self._workers_lock:
            workers = list(self._workers)
            self._workers.clear()
        for worker in workers:
            worker.join(self.timeout)

    def _poke_listener(self) -> None:
        _, win32file_mod, _, _ = _require_windows_modules()
        handle = None
        try:
            handle = win32file_mod.CreateFile(
                self._pipe_name,
                win32file_mod.GENERIC_READ | win32file_mod.GENERIC_WRITE,
                0,
                None,
                win32file_mod.OPEN_EXISTING,
                0,
                None,
            )
        except pywintypes.error:  # type: ignore[attr-defined]
            return
        if handle is not None:
            win32file_mod.CloseHandle(handle)


def CallbackIPCServer(  # noqa: N802 - public API retains class-style name
    socket_path: Path,
    handler: t.Callable[[Invocation], Response],
    passthrough_handler: t.Callable[[PassthroughResult], Response],
    *,
    timeouts: TimeoutConfig | None = None,
) -> BaseIPCServer:
    """Return an IPC server implementation appropriate for the platform."""
    timeouts = timeouts or TimeoutConfig()
    handlers = IPCHandlers(handler=handler, passthrough_handler=passthrough_handler)
    server_cls: type[BaseIPCServer] = (
        NamedPipeServer if _should_use_named_pipe(socket_path) else IPCServer
    )
    return server_cls(
        socket_path,
        timeout=timeouts.timeout,
        accept_timeout=timeouts.accept_timeout,
        handlers=handlers,
    )


def _should_use_named_pipe(socket_path: Path) -> bool:
    raw = str(socket_path)
    return IS_WINDOWS and raw.startswith(NamedPipeServer._PIPE_PREFIX)


_RequestProcessor = t.Callable[[BaseIPCServer, t.Any], Response]

_REQUEST_HANDLERS: dict[str, tuple[_RequestValidator, _RequestProcessor]] = {
    KIND_INVOCATION: (validate_invocation_payload, _process_invocation),
    KIND_PASSTHROUGH_RESULT: (
        validate_passthrough_payload,
        _process_passthrough_result,
    ),
}


def _process_request(
    server: BaseIPCServer, processor: _RequestProcessor, obj: object
) -> Response:
    try:
        return processor(server, obj)
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as exc:  # pragma: no cover - defensive logging
        logger.exception("IPC handler raised an exception")
        message = str(exc) or exc.__class__.__name__
        return Response(stderr=message, exit_code=1)


def _handle_raw_request(server: BaseIPCServer, raw: bytes) -> Response | None:
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

    return _process_request(server, processor, obj)


class _IPCHandler(socketserver.StreamRequestHandler):
    """Handle a single shim connection."""

    def handle(self) -> None:  # pragma: no cover - exercised via behaviour tests
        raw = self.rfile.read()
        response = _handle_raw_request(self.server.outer, raw)  # type: ignore[attr-defined]
        if response is None:
            return
        self.wfile.write(json.dumps(response.to_dict()).encode("utf-8"))
        self.wfile.flush()


class _InnerServer(_BaseUnixServer):
    """Threaded Unix stream server passing requests to :class:`BaseIPCServer`."""

    daemon_threads = True

    def __init__(self, socket_path: Path, outer: BaseIPCServer) -> None:
        self.outer = outer
        super().__init__(str(socket_path), _IPCHandler)


__all__ = [
    "CallbackIPCServer",
    "IPCHandlers",
    "IPCServer",
    "NamedPipeServer",
    "TimeoutConfig",
]
