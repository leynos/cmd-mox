"""Windows named pipe IPC implementation for CmdMox."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import json
import logging
import threading
import typing as t
from pathlib import Path
from typing_extensions import TypeVar

try:
    import win32file
    import win32pipe
    import pywintypes
    import winerror
    import win32con
    WINDOWS_AVAILABLE = True
except ImportError:
    WINDOWS_AVAILABLE = False

from cmd_mox._validators import (
    validate_optional_timeout,
    validate_positive_finite_timeout,
)

from .constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from .json_utils import parse_json_safely, validate_invocation_payload, validate_passthrough_payload
from .models import Invocation, PassthroughResult, Response

logger = logging.getLogger(__name__)

_RequestValidator = t.Callable[[dict[str, t.Any]], t.Any | None]
_RequestProcessor = t.Callable[['WindowsNamedPipeServer', t.Any], Response]
_DispatchArg = t.TypeVar("_DispatchArg", Invocation, PassthroughResult)


def _process_invocation(server: WindowsNamedPipeServer, invocation: Invocation) -> Response:
    """Invoke :meth:`WindowsNamedPipeServer.handle_invocation` for *invocation*."""
    return server.handle_invocation(invocation)


def _process_passthrough_result(
    server: WindowsNamedPipeServer, result: PassthroughResult
) -> Response:
    """Invoke :meth:`WindowsNamedPipeServer.handle_passthrough_result` for *result*."""
    return server.handle_passthrough_result(result)


@dc.dataclass(slots=True)
class WindowsIPCHandlers:
    """Optional callbacks customising :class:`WindowsNamedPipeServer` behaviour."""

    handler: t.Callable[['WindowsNamedPipeServer', Invocation], Response] | None = None
    passthrough_handler: t.Callable[['WindowsNamedPipeServer', PassthroughResult], Response] | None = None


@dc.dataclass(slots=True)
class WindowsTimeoutConfig:
    """Timeout configuration for Windows named pipe operations."""

    timeout: float = 5.0
    accept_timeout: float | None = None

    def __post_init__(self) -> None:
        """Validate timeout values to catch misconfiguration early."""
        validate_positive_finite_timeout(self.timeout)
        validate_optional_timeout(self.accept_timeout, name="accept_timeout")


class WindowsNamedPipeServer:
    """Windows named pipe server for shims.
    
    The server listens on a Windows named pipe for IPC communication.
    Clients connect via the named pipe path and communicate using JSON messages.
    Connection attempts default to a five second timeout.
    """

    def __init__(
        self,
        pipe_name: str,
        timeout: float = 5.0,
        accept_timeout: float | None = None,
        *,
        handlers: WindowsIPCHandlers | None = None,
    ) -> None:
        """Create a server listening on *pipe_name*."""
        if not WINDOWS_AVAILABLE:
            msg = "pywin32 is required for Windows named pipe support"
            raise RuntimeError(msg)
        
        self.pipe_name = pipe_name
        validate_positive_finite_timeout(timeout)
        validate_optional_timeout(accept_timeout, name="accept_timeout")
        self.timeout = timeout
        self.accept_timeout = accept_timeout or min(0.1, timeout / 10)
        self._pipe_handle: int | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        handlers = handlers or WindowsIPCHandlers()
        self._handler = handlers.handler
        self._passthrough_handler = handlers.passthrough_handler
        self._clients: set[int] = set()

    def _dispatch(
        self,
        handler: t.Callable[['WindowsNamedPipeServer', _DispatchArg], Response] | None,
        argument: _DispatchArg,
        *,
        default: t.Callable[['WindowsNamedPipeServer', _DispatchArg], Response],
        error_builder: t.Callable[['WindowsNamedPipeServer', _DispatchArg, Exception], RuntimeError]
        | None = None,
    ) -> Response:
        """Invoke *handler* when provided, otherwise fall back to *default*."""
        if handler is None:
            return default(self, argument)
        if error_builder is None:
            return handler(self, argument)
        try:
            return handler(self, argument)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            raise error_builder(self, argument, exc) from exc

    @staticmethod
    def _default_invocation_response(server: WindowsNamedPipeServer, invocation: Invocation) -> Response:
        """Echo the command name when no handler overrides the behaviour."""
        return Response(stdout=invocation.command)

    @staticmethod
    def _raise_unhandled_passthrough(server: WindowsNamedPipeServer, result: PassthroughResult) -> Response:
        """Raise when passthrough results lack a configured handler."""
        msg = f"Unhandled passthrough result for {result.invocation_id}"
        raise RuntimeError(msg)

    @staticmethod
    def _build_passthrough_error(
        server: WindowsNamedPipeServer, result: PassthroughResult, exc: Exception
    ) -> RuntimeError:
        """Create the wrapped passthrough error surfaced to callers."""
        msg = f"Exception in passthrough handler for {result.invocation_id}: {exc}"
        return RuntimeError(msg)

    def __enter__(self) -> WindowsNamedPipeServer:
        """Start the server when entering a context."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: t.Any,  # TracebackType
    ) -> None:
        """Stop the server when leaving a context."""
        self.stop()

    def _create_named_pipe(self) -> int:
        """Create a Windows named pipe with appropriate security settings."""
        pipe_name = self.pipe_name
        if not pipe_name.startswith(r"\\.\pipe\\"):
            pipe_name = r"\\.\pipe\\" + pipe_name
        
        try:
            pipe_handle =                 win32pipe.CreateNamedPipe(
                pipe_name,
                win32pipe.PIPE_ACCESS_DUPLEX,
                win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
                win32pipe.PIPE_UNLIMITED_INSTANCES,
                65536,  # Out buffer size
                65536,  # In buffer size
                int(self.accept_timeout * 1000),  # Timeout in milliseconds
                pywintypes.SECURITY_ATTRIBUTES()  # Default security attributes
            )
            
            if pipe_handle == win32file.INVALID_HANDLE_VALUE:
                raise RuntimeError(f"Failed to create named pipe: {pipe_name}")
                
            return pipe_handle
            
        except pywintypes.error as e:
            raise RuntimeError(f"Failed to create named pipe {pipe_name}: {e}") from e

    def _handle_client_connection(self, pipe_handle: int) -> None:
        """Handle a single client connection on the named pipe."""
        try:
            # Read request from client
            result, data = win32file.ReadFile(pipe_handle, 65536)
            if result != 0:
                logger.error(f"Failed to read from pipe: {result}")
                return
            
            if isinstance(data, bytes):
                raw_request = data.decode('utf-8')
            else:
                raw_request = data
            logger.debug(f"Received request: {raw_request[:200]}")
            
            # Parse JSON request
            payload = parse_json_safely(data if isinstance(data, bytes) else data.encode('utf-8'))
            if payload is None:
                logger.error("Failed to parse JSON request")
                return
            
            copied_payload = payload.copy()
            kind = str(copied_payload.pop("kind", KIND_INVOCATION))
            
            # Route to appropriate handler
            handler_entry = _REQUEST_HANDLERS.get(kind)
            if handler_entry is None:
                logger.error("Unknown IPC payload kind: %r", kind)
                return
            
            validator, processor = handler_entry
            obj = validator(copied_payload)
            if obj is None:
                return
            
            response = self._process_request(processor, obj)
            
            # Send response back to client
            response_data = json.dumps(response.to_dict()).encode('utf-8')
            win32file.WriteFile(pipe_handle, response_data)
            
        except pywintypes.error as e:
            if e.winerror == winerror.ERROR_BROKEN_PIPE:
                logger.debug("Client disconnected normally")
            elif e.winerror == winerror.ERROR_PIPE_NOT_CONNECTED:
                logger.debug("Pipe not connected")
            else:
                logger.exception("Windows named pipe error")
        except Exception:
            logger.exception("Unexpected error handling client connection")
        finally:
            try:
                win32file.CloseHandle(pipe_handle)
            except pywintypes.error:
                pass

    def _process_request(self, processor: _RequestProcessor, obj: object) -> Response:
        """Execute *processor* and wrap unexpected failures."""
        try:
            return processor(self, obj)
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as exc:
            logger.exception("IPC handler raised an exception")
            message = str(exc) or exc.__class__.__name__
            return Response(stderr=message, exit_code=1)

    def _server_loop(self) -> None:
        """Main server loop that accepts connections and handles requests."""
        logger.info(f"Starting Windows named pipe server: {self.pipe_name}")
        
        while not self._stop_event.is_set():
            try:
                # Create a new pipe instance for this connection
                pipe_handle = self._create_named_pipe()
                
                # Wait for client connection
                try:
                    win32pipe.ConnectNamedPipe(pipe_handle, None)
                except pywintypes.error as e:
                    if e.winerror == winerror.ERROR_PIPE_CONNECTED:
                        # Client already connected, continue
                        pass
                    elif e.winerror == winerror.ERROR_OPERATION_ABORTED:
                        # Server stopped
                        break
                    else:
                        logger.error(f"ConnectNamedPipe failed: {e}")
                        win32file.CloseHandle(pipe_handle)
                        continue
                
                # Handle client connection in a separate thread
                client_thread = threading.Thread(
                    target=self._handle_client_connection,
                    args=(pipe_handle,),
                    daemon=True
                )
                client_thread.start()
                self._clients.add(pipe_handle)
                
            except Exception:
                logger.exception("Error in server loop")
                if not self._stop_event.is_set():
                    # Brief pause before retrying on error
                    self._stop_event.wait(0.1)

    def start(self) -> None:
        """Start the background server thread."""
        with self._lock:
            if self._thread:
                msg = "Windows named pipe server already started"
                raise RuntimeError(msg)
            
            self._stop_event.clear()
            thread = threading.Thread(target=self._server_loop, daemon=True)
            self._thread = thread
        
        thread.start()
        
        # Wait a brief moment to ensure server is ready
        import time
        time.sleep(0.1)

    def stop(self) -> None:
        """Stop the server and clean up all connections."""
        with self._lock:
            thread = self._thread
            self._thread = None
        
        if thread:
            self._stop_event.set()
            thread.join(self.timeout)

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


class CallbackWindowsNamedPipeServer(WindowsNamedPipeServer):
    """WindowsNamedPipeServer variant that delegates to callbacks."""

    def __init__(
        self,
        pipe_name: str,
        handler: t.Callable[[Invocation], Response],
        passthrough_handler: t.Callable[[PassthroughResult], Response],
        *,
        timeouts: WindowsTimeoutConfig | None = None,
    ) -> None:
        """Initialise a callback-driven Windows named pipe server."""
        timeouts = timeouts or WindowsTimeoutConfig()
        
        def handler_wrapper(_server: WindowsNamedPipeServer, invocation: Invocation) -> Response:
            return handler(invocation)
            
        def passthrough_handler_wrapper(_server: WindowsNamedPipeServer, result: PassthroughResult) -> Response:
            return passthrough_handler(result)
        
        super().__init__(
            pipe_name,
            timeout=timeouts.timeout,
            accept_timeout=timeouts.accept_timeout,
            handlers=WindowsIPCHandlers(
                handler=handler_wrapper,
                passthrough_handler=passthrough_handler_wrapper,
            ),
        )


_REQUEST_HANDLERS: dict[str, tuple[_RequestValidator, _RequestProcessor]] = {
    KIND_INVOCATION: (validate_invocation_payload, _process_invocation),
    KIND_PASSTHROUGH_RESULT: (
        validate_passthrough_payload,
        _process_passthrough_result,
    ),
}


def is_windows_ipc_available() -> bool:
    """Return True if Windows IPC components are available."""
    return WINDOWS_AVAILABLE


__all__ = [
    "CallbackWindowsNamedPipeServer",
    "WindowsIPCHandlers",
    "WindowsNamedPipeServer",
    "WindowsTimeoutConfig",
    "is_windows_ipc_available",
]