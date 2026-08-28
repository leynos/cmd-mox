"""Windows named-pipe transport for the shared IPC request pipeline."""

from __future__ import annotations

import contextlib
import importlib
import logging
import threading
import time
import typing as typ

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc.windows import (
    ERROR_BROKEN_PIPE,
    ERROR_FILE_NOT_FOUND,
    ERROR_NO_DATA,
    ERROR_OPERATION_ABORTED,
    ERROR_PIPE_BUSY,
    ERROR_PIPE_CONNECTED,
    PIPE_CHUNK_SIZE,
    derive_pipe_name,
    read_pipe_message,
    write_pipe_payload,
)

from ._server_core import (
    IPCHandlers,
    TimeoutConfig,
    _BaseIPCServer,
    _request_pipeline,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from .models import Invocation, PassthroughResult, Response

if path_utils.IS_WINDOWS:  # pragma: win32-only
    try:
        pywintypes = importlib.import_module("pywintypes")
        win32file = importlib.import_module("win32file")
        win32pipe = importlib.import_module("win32pipe")
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        msg = "pywin32 is required for Windows named pipe support"
        raise RuntimeError(msg) from exc
else:  # pragma: no cover - non-Windows fallback for type-checkers
    pywintypes = typ.cast("typ.Any", None)
    win32file = typ.cast("typ.Any", None)
    win32pipe = typ.cast("typ.Any", None)

logger = logging.getLogger(__name__)


class NamedPipeServer(_BaseIPCServer["_NamedPipeState"]):
    """Windows named-pipe variant of :class:`IPCServer`."""

    _pipe_name: str

    def __init__(
        self,
        socket_path: Path,
        timeout: float = 5.0,
        accept_timeout: float | None = None,
        *,
        handlers: IPCHandlers | None = None,
    ) -> None:
        if not path_utils.IS_WINDOWS:
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
        pass

    def _create_backend(self) -> tuple[_NamedPipeState, threading.Thread]:
        state = _NamedPipeState(
            pipe_name=self._pipe_name,
            outer=self,
            accept_timeout=self.accept_timeout,
        )
        thread = threading.Thread(target=state.serve_forever, daemon=True)
        return state, thread

    def _wait_until_ready(self) -> None:
        state = self._server
        if state is None:
            return
        if not state.ready_event.wait(self.timeout):
            state.stop()
            msg = (
                f"Named pipe {self._pipe_name} not accepting connections within timeout"
            )
            raise RuntimeError(msg)

    def _stop_backend(self, server: _NamedPipeState | None) -> None:
        if server is None:
            return
        server.stop()
        server.join_clients(self.timeout)


class CallbackNamedPipeServer(NamedPipeServer):
    """Callback-based helper mirroring :class:`CallbackIPCServer`."""

    def __init__(
        self,
        socket_path: Path,
        handler: cabc.Callable[[Invocation], Response],
        passthrough_handler: cabc.Callable[[PassthroughResult], Response],
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
    """Stateful helper managing named-pipe connections and worker threads."""

    def __init__(
        self,
        *,
        pipe_name: str,
        outer: _BaseIPCServer[_NamedPipeState],
        accept_timeout: float,
    ) -> None:
        self.pipe_name, self.outer = pipe_name, outer
        self.accept_timeout = accept_timeout
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        self._client_threads: set[threading.Thread] = set()
        self._client_lock = threading.Lock()

    def _try_connect_pipe(self, handle: object) -> tuple[bool, bool]:
        """Attempt to connect *handle* to the named pipe.

        Returns
        -------
        tuple[bool, bool]
            ``(keep_serving, connected)``: whether the accept loop should
            continue, and whether a client is now attached to *handle*.
        """
        try:
            win32pipe.ConnectNamedPipe(handle, None)
        except pywintypes.error as exc:
            return self._handle_connection_error(exc, handle)
        return True, True

    def _handle_connection_error(
        self, exc: BaseException, handle: object
    ) -> tuple[bool, bool]:
        """Return control-flow decisions for a failed connection attempt.

        Returns
        -------
        tuple[bool, bool]
            ``(keep_serving, connected)``: whether the accept loop should
            continue, and whether *handle* is usable despite *exc*.
        """
        winerror = getattr(exc, "winerror", None)
        if winerror is None:
            logger.error("Named pipe connect failed", exc_info=exc)
            self._close_handle(handle)
            return True, False
        if winerror == ERROR_PIPE_CONNECTED:
            return True, True
        if winerror in {ERROR_OPERATION_ABORTED, ERROR_NO_DATA}:
            self._close_handle(handle)
            return False, False
        logger.error("Named pipe connect failed", exc_info=exc)
        self._close_handle(handle)
        return True, False

    @staticmethod
    def _close_handle(handle: object) -> None:
        win32file.CloseHandle(handle)

    def _spawn_handler_thread(self, handle: object) -> None:
        """Create and track the per-client handler thread."""
        thread = threading.Thread(
            target=self._handle_client,
            args=(handle,),
            daemon=True,
        )
        with self._client_lock:
            self._client_threads.add(thread)
        thread.start()

    def _get_active_threads(self) -> list[threading.Thread]:
        """Get a snapshot of active client threads.

        Returns
        -------
        list[threading.Thread]
            A copy of the tracked per-client handler threads.
        """
        with self._client_lock:
            return list(self._client_threads)

    @staticmethod
    def _calculate_remaining_time(deadline: float) -> float | None:
        """Calculate remaining time until deadline.

        Returns None if deadline has passed, otherwise remaining seconds.

        Returns
        -------
        float | None
            The remaining seconds, or ``None`` when the deadline has passed.
        """
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        return remaining

    def _join_thread_with_deadline(
        self, thread: threading.Thread, deadline: float
    ) -> bool:
        """Join a thread respecting the deadline.

        Returns True if join attempted, False if deadline expired before join.

        Returns
        -------
        bool
            Whether joining was attempted before the deadline expired.
        """
        remaining = self._calculate_remaining_time(deadline)
        if remaining is None:
            return False
        thread.join(max(0.0, remaining))
        return True

    def _join_all_threads_with_deadline(
        self, threads: list[threading.Thread], deadline: float
    ) -> bool:
        """Join all threads respecting the deadline.

        Returns True if all threads were processed, False if deadline expired.

        Returns
        -------
        bool
            Whether every thread's join was attempted before the deadline expired.
        """
        for thread in threads:
            if not self._join_thread_with_deadline(thread, deadline):
                return False
        return True

    def serve_forever(self) -> None:
        if not path_utils.IS_WINDOWS:  # pragma: no cover - defensive guard
            return

        while not self.stop_event.is_set():
            try:
                handle = self._create_pipe_instance()
            except pywintypes.error:
                logger.exception("Named pipe accept failed")
                self.ready_event.set()
                break
            if not self.ready_event.is_set():
                self.ready_event.set()
            should_continue, should_handle = self._try_connect_pipe(handle)
            if not should_continue:
                break
            if not should_handle:
                continue

            if self.stop_event.is_set():
                win32file.CloseHandle(handle)
                break

            self._spawn_handler_thread(handle)

    def stop(self) -> None:
        if self.stop_event.is_set():
            return
        self.stop_event.set()
        self.ready_event.set()
        self._poke_pipe()

    def join_clients(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            threads = self._get_active_threads()
            if not threads:
                return
            if self._calculate_remaining_time(deadline) is None:
                return
            if not self._join_all_threads_with_deadline(threads, deadline):
                return

    def _create_pipe_instance(self) -> object:
        timeout_ms = max(1, int(self.accept_timeout * 1000))
        return win32pipe.CreateNamedPipe(
            self.pipe_name,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE
            | win32pipe.PIPE_READMODE_MESSAGE
            | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
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
            response_bytes = _request_pipeline(self.outer, raw, "named_pipe")
            if response_bytes is not None:
                write_pipe_payload(
                    handle,
                    response_bytes,
                    win32file=win32file,
                )
        except pywintypes.error as exc:
            if exc.winerror not in {ERROR_BROKEN_PIPE, ERROR_NO_DATA}:
                logger.exception("Named pipe handler failed")
        finally:
            with contextlib.suppress(pywintypes.error):
                win32pipe.DisconnectNamedPipe(handle)
            win32file.CloseHandle(handle)
            with self._client_lock:
                self._client_threads.discard(thread)

    @staticmethod
    def _read_request(handle: object) -> bytes | None:
        return read_pipe_message(
            handle,
            win32file=win32file,
            pywintypes=pywintypes,
            chunk_size=PIPE_CHUNK_SIZE,
        )

    def _poke_pipe(self) -> None:
        try:
            handle = win32file.CreateFile(
                self.pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
        except pywintypes.error as exc:
            if exc.winerror not in {ERROR_PIPE_BUSY, ERROR_FILE_NOT_FOUND}:
                logger.debug("Named pipe wakeup failed: %s", exc)
            return
        win32file.CloseHandle(handle)
