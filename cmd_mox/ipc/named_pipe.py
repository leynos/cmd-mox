"""Windows named-pipe transport for the shared IPC request pipeline."""

from __future__ import annotations

import contextlib
import functools
import importlib
import logging
import threading
import time
import typing as typ

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc.windows import (
    ERROR_BROKEN_PIPE,
    ERROR_FILE_NOT_FOUND,
    ERROR_IO_PENDING,
    ERROR_MORE_DATA,
    ERROR_NO_DATA,
    ERROR_OPERATION_ABORTED,
    ERROR_PIPE_BUSY,
    ERROR_PIPE_CONNECTED,
    MAX_MESSAGE_SIZE,
    PIPE_CHUNK_SIZE,
    PipeMessageTooLargeError,
    PipeReadOptions,
    derive_pipe_name,
    read_pipe_message,
    write_pipe_payload,
)

from . import _observability
from ._named_pipe_limits import (
    CLIENT_READ_TIMEOUT_SECONDS,
    MAX_ACTIVE_CLIENTS,
    ClientSlot,
    PipeReadCancelled,
    WorkerEvent,
    acquire_client_slot,
    emit_worker_event,
    join_threads_before,
    remaining_ms,
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
        win32event = importlib.import_module("win32event")
        win32file = importlib.import_module("win32file")
        win32pipe = importlib.import_module("win32pipe")
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        msg = "pywin32 is required for Windows named pipe support"
        raise RuntimeError(msg) from exc
else:  # pragma: no cover - non-Windows fallback for type-checkers
    pywintypes = typ.cast("typ.Any", None)
    win32event = typ.cast("typ.Any", None)
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
        # The accept loop sets ``ready_event`` on its way out of a failed pipe
        # creation too, so the event alone would report a dead server as ready;
        # ``startup_failed`` is what distinguishes the two.
        ready = state.ready_event.wait(self.timeout)
        if ready and not state.startup_failed:
            return
        state.stop()
        reason = (
            "failed to create its first pipe instance"
            if ready
            else "not accepting connections within timeout"
        )
        msg = f"Named pipe {self._pipe_name} {reason}"
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
    """Stateful helper managing named-pipe connections and worker threads.

    Concurrency, message size, and client lifetime are all bounded here:

    * at most :data:`MAX_ACTIVE_CLIENTS` workers run at once, enforced by a
      :class:`threading.BoundedSemaphore` acquired before a worker is spawned;
    * a request may not exceed :data:`~cmd_mox.ipc.windows.MAX_MESSAGE_SIZE`;
    * a client has :data:`CLIENT_READ_TIMEOUT_SECONDS` to deliver its request.

    Cancellation design
    -------------------
    Pipe instances are created with ``FILE_FLAG_OVERLAPPED`` and every blocking
    operation (``ConnectNamedPipe`` and ``ReadFile``) is issued asynchronously,
    then awaited with ``WaitForMultipleObjects`` on the operation's event *and*
    a Win32 shutdown event. A deadline or a :meth:`stop` therefore wakes the
    waiting thread itself, which cancels the request with ``CancelIoEx`` and
    drains it with ``GetOverlappedResult`` before releasing the buffer. This is
    preferred to a watchdog thread because the blocked thread performs its own
    cancellation and unwinds, so no thread is ever left stranded in the kernel,
    and to a plain synchronous read because a synchronous ``ReadFile`` cannot
    be given a deadline from within its own thread.

    ``ConnectNamedPipe`` must be issued with an ``OVERLAPPED`` on an overlapped
    handle: passing ``None`` there is documented to report completion
    incorrectly, so the accept path awaits the connect event explicitly.
    """

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
        # Set when the accept loop never created its first pipe instance.
        self.startup_failed = False
        self._client_threads: set[threading.Thread] = set()
        self._client_lock = threading.Lock()
        self._client_slots = threading.BoundedSemaphore(MAX_ACTIVE_CLIENTS)
        self._stop_handle: object | None = None
        self._stop_handle_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Overlapped I/O plumbing
    # ------------------------------------------------------------------

    def _win32_stop_handle(self) -> object:
        """Return the Win32 event signalled when the server stops.

        The handle is created lazily so that constructing the state object
        stays possible on platforms without ``pywin32``.

        Returns
        -------
        object
            A manual-reset Win32 event handle, already signalled when
            :meth:`stop` has run.
        """
        with self._stop_handle_lock:
            if self._stop_handle is None:
                # CreateEvent(security, manual_reset, initial_state, name).
                self._stop_handle = win32event.CreateEvent(
                    None,
                    True,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
                    False,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
                    None,
                )
                if self.stop_event.is_set():
                    win32event.SetEvent(self._stop_handle)
            return self._stop_handle

    @staticmethod
    def _create_overlapped() -> tuple[typ.Any, object]:
        """Build an ``OVERLAPPED`` with a dedicated manual-reset event.

        Returns
        -------
        tuple[typing.Any, object]
            The overlapped structure and its event handle. The caller owns the
            event and must close it.
        """
        overlapped = pywintypes.OVERLAPPED()
        # CreateEvent(security, manual_reset, initial_state, name).
        event = win32event.CreateEvent(
            None,
            True,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
            False,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
            None,
        )
        overlapped.hEvent = event
        return overlapped, event

    @staticmethod
    def _cancel_overlapped(handle: object, overlapped: object) -> None:
        """Cancel a pending overlapped operation and wait for it to settle.

        Draining with ``GetOverlappedResult`` before the caller releases the
        buffer guarantees the kernel is no longer writing into it.
        """
        with contextlib.suppress(pywintypes.error):
            win32file.CancelIoEx(handle, overlapped)
        with contextlib.suppress(pywintypes.error):
            # GetOverlappedResult(handle, overlapped, wait).
            win32file.GetOverlappedResult(
                handle,
                overlapped,
                True,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
            )

    def _wait_for_overlapped(self, event: object, timeout_ms: int) -> int:
        """Wait for *event* or shutdown, whichever comes first.

        Returns
        -------
        int
            The ``WaitForMultipleObjects`` result code.
        """
        # WaitForMultipleObjects(handles, wait_all, timeout_ms).
        return win32event.WaitForMultipleObjects(
            [event, self._win32_stop_handle()],
            False,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
            timeout_ms,
        )

    # ------------------------------------------------------------------
    # Accept loop
    # ------------------------------------------------------------------

    def _try_connect_pipe(self, handle: object) -> tuple[bool, bool]:
        """Attempt to connect *handle* to the named pipe.

        Returns
        -------
        tuple[bool, bool]
            ``(keep_serving, connected)``: whether the accept loop should
            continue, and whether a client is now attached to *handle*.
        """
        overlapped, event = self._create_overlapped()
        try:
            try:
                status = win32pipe.ConnectNamedPipe(handle, overlapped)
            except pywintypes.error as exc:
                return self._handle_connection_error(exc, handle)
            if status == ERROR_IO_PENDING:
                return self._await_connection(handle, overlapped, event)
            return True, True
        finally:
            win32file.CloseHandle(event)

    def _await_connection(
        self, handle: object, overlapped: object, event: object
    ) -> tuple[bool, bool]:
        """Block until a client attaches to *handle* or the server stops.

        Returns
        -------
        tuple[bool, bool]
            ``(keep_serving, connected)`` for the accept loop.
        """
        if self._wait_for_overlapped(event, win32event.INFINITE) != (
            win32event.WAIT_OBJECT_0
        ):
            self._cancel_overlapped(handle, overlapped)
            self._close_handle(handle)
            return False, False
        try:
            # GetOverlappedResult(handle, overlapped, wait).
            win32file.GetOverlappedResult(
                handle,
                overlapped,
                True,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
            )
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

    @staticmethod
    def _dispose_handle(handle: object) -> None:
        """Disconnect and close a client *handle*, tolerating pipe errors.

        Closing is tolerant because the cancel-and-drain path may already have
        closed the handle; a second close must not escape a worker thread.
        """
        with contextlib.suppress(pywintypes.error):
            win32pipe.DisconnectNamedPipe(handle)
        with contextlib.suppress(pywintypes.error):
            win32file.CloseHandle(handle)

    def _admit_client(self, handle: object) -> None:
        """Hand *handle* to a worker thread, or refuse it.

        Admission is refused when the client limit has been reached, when the
        interpreter cannot start a thread, and when shutdown has begun. Refusal
        is never fatal to the accept loop: the handle is disposed of and serving
        continues.
        """
        slot = acquire_client_slot(self._client_slots)
        if slot is None:
            self._refuse_client(handle, None, "ClientLimitReached")
            return
        try:
            admitted = self._spawn_handler_thread(handle, slot)
        except RuntimeError:
            # The interpreter refused a new thread, so the worker will never
            # run and cannot release the permit; do it here instead.
            logger.exception("Named pipe handler thread could not start")
            self._refuse_client(handle, slot, "ThreadStartFailed")
            return
        if not admitted:
            self._refuse_client(handle, slot, "ServerStopping")

    def _refuse_client(
        self, handle: object, slot: ClientSlot | None, error_category: str
    ) -> None:
        """Report a refused admission and release everything it reserved.

        The permit is released exactly once by :meth:`ClientSlot.release`, and
        the handle is disposed of, because no worker will ever own either.
        """
        if slot is not None:
            slot.release()
        emit_worker_event(WorkerEvent("rejected", error_category=error_category))
        self._dispose_handle(handle)

    def _spawn_handler_thread(
        self, handle: object, slot: ClientSlot | None = None
    ) -> bool:
        """Create and track the per-client handler thread.

        Registration re-checks ``stop_event`` while holding ``_client_lock``,
        the same lock :meth:`stop` sets the event under. Either stop wins and
        the admission is refused, or registration completes first and the
        snapshot :meth:`join_clients` takes is guaranteed to include the
        thread; no worker can slip in behind an emptied snapshot.

        Returns
        -------
        bool
            Whether the worker was registered and started. ``False`` means
            shutdown began first and the caller must undo the admission.

        Raises
        ------
        RuntimeError
            If the interpreter cannot start a new thread. The thread is
            untracked again before the error propagates.
        """
        thread = threading.Thread(
            target=self._handle_client,
            args=(handle, slot),
            daemon=True,
        )
        with self._client_lock:
            if self.stop_event.is_set():
                return False
            self._client_threads.add(thread)
        try:
            thread.start()
        except RuntimeError:
            with self._client_lock:
                self._client_threads.discard(thread)
            raise
        return True

    def _get_active_threads(self) -> list[threading.Thread]:
        """Get a snapshot of active client threads.

        Returns
        -------
        list[threading.Thread]
            A copy of the tracked per-client handler threads.
        """
        with self._client_lock:
            return list(self._client_threads)

    def serve_forever(self) -> None:
        if not path_utils.IS_WINDOWS:  # pragma: no cover - defensive guard
            return

        while not self.stop_event.is_set():
            try:
                handle = self._create_pipe_instance()
            except pywintypes.error:
                logger.exception("Named pipe accept failed")
                self.startup_failed = not self.ready_event.is_set()
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

            self._admit_client(handle)

    def stop(self) -> None:
        # ``stop_event`` is set under ``_client_lock`` so that admission and
        # shutdown are mutually exclusive: see ``_spawn_handler_thread``.
        with self._client_lock:
            if self.stop_event.is_set():
                return
            self.stop_event.set()
        self.ready_event.set()
        self._signal_stop_handle()
        # The Win32 event already unblocks the accept wait and any active read;
        # the poke remains as a cheap belt-and-braces wakeup for a pipe instance
        # created before the shutdown event existed.
        self._poke_pipe()

    def _signal_stop_handle(self) -> None:
        """Wake any thread waiting on the Win32 shutdown event.

        A handle created after this point observes the already-set Python
        event and is signalled at creation, so no wakeup can be lost.
        """
        with self._stop_handle_lock:
            handle = self._stop_handle
        if handle is not None:
            win32event.SetEvent(handle)

    def join_clients(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while True:
            threads = self._get_active_threads()
            if not threads:
                return
            if not join_threads_before(threads, deadline):
                return

    def _create_pipe_instance(self) -> object:
        timeout_ms = max(1, int(self.accept_timeout * 1000))
        return win32pipe.CreateNamedPipe(
            self.pipe_name,
            # Overlapped I/O is mandatory for cancellation: an overlapped read
            # issued on a synchronous handle completes synchronously, so
            # CancelIoEx would have nothing to cancel and the deadline could
            # not be enforced.
            win32pipe.PIPE_ACCESS_DUPLEX | win32file.FILE_FLAG_OVERLAPPED,
            win32pipe.PIPE_TYPE_MESSAGE
            | win32pipe.PIPE_READMODE_MESSAGE
            | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            PIPE_CHUNK_SIZE,
            PIPE_CHUNK_SIZE,
            timeout_ms,
            None,
        )

    def _handle_client(self, handle: object, slot: ClientSlot | None = None) -> None:
        """Serve one client, always disposing of its handle and permit.

        The ``finally`` block is the single release point for *slot*, so every
        exit path — success, handler error, read failure, oversize rejection,
        timeout, and shutdown — returns exactly one permit.
        """
        thread = threading.current_thread()
        started = time.perf_counter()
        emit_worker_event(WorkerEvent("admitted"))
        try:
            self._serve_one_request(handle, started)
        finally:
            # Disposal is nested so that a failing CloseHandle - plausible when
            # the cancel-and-drain path has already closed the handle - cannot
            # skip the permit release and leak capacity permanently.
            try:
                self._dispose_handle(handle)
            finally:
                with self._client_lock:
                    self._client_threads.discard(thread)
                if slot is not None:
                    slot.release()

    def _serve_one_request(self, handle: object, started: float) -> None:
        """Read, dispatch, and answer one request, emitting its outcome."""
        try:
            raw = self._read_request(handle)
            if raw is not None:
                response_bytes = _request_pipeline(self.outer, raw, "named_pipe")
                if response_bytes is not None:
                    write_pipe_payload(handle, response_bytes, win32file=win32file)
        except PipeMessageTooLargeError as exc:
            # The limit trips before any envelope is parsed, so no correlation
            # id exists; only the byte count is reported, never the bytes.
            emit_worker_event(
                WorkerEvent(
                    "read_size_rejected",
                    error_category="PipeMessageTooLargeError",
                    message_size=exc.received,
                )
            )
        except PipeReadCancelled as exc:
            emit_worker_event(self._cancellation_event(exc, started))
        except pywintypes.error as exc:
            if exc.winerror not in {ERROR_BROKEN_PIPE, ERROR_NO_DATA}:
                logger.exception("Named pipe handler failed")
            emit_worker_event(
                WorkerEvent(
                    "completed",
                    error_category=self._pipe_error_category(exc),
                    duration_ms=_observability.elapsed_ms(started),
                )
            )
        else:
            emit_worker_event(
                WorkerEvent("completed", duration_ms=_observability.elapsed_ms(started))
            )

    @staticmethod
    def _cancellation_event(exc: PipeReadCancelled, started: float) -> WorkerEvent:
        """Describe a cancelled read as a bounded worker event.

        Returns
        -------
        WorkerEvent
            A ``timeout`` event when the deadline expired, otherwise a
            ``completed`` event attributed to server shutdown.
        """
        if exc.timed_out:
            return WorkerEvent("timeout", error_category="ReadTimeout")
        return WorkerEvent(
            "completed",
            error_category="ServerStopped",
            duration_ms=_observability.elapsed_ms(started),
        )

    @staticmethod
    def _pipe_error_category(exc: BaseException) -> str:
        """Map a pywin32 error to a closed-set failure label.

        Returns
        -------
        str
            One of ``"BrokenPipe"``, ``"NoData"``, or ``"PipeError"``. The
            exception message is never used.
        """
        winerror = getattr(exc, "winerror", None)
        if winerror == ERROR_BROKEN_PIPE:
            return "BrokenPipe"
        if winerror == ERROR_NO_DATA:
            return "NoData"
        return "PipeError"

    def _read_request(self, handle: object) -> bytes | None:
        """Read one size-bounded request from *handle* within the deadline.

        Propagates :class:`~cmd_mox.ipc.windows.PipeMessageTooLargeError` past
        :data:`~cmd_mox.ipc.windows.MAX_MESSAGE_SIZE` bytes, and
        :class:`PipeReadCancelled` when the deadline expires or the server
        stops first.

        Returns
        -------
        bytes | None
            The raw request bytes.
        """
        deadline = time.monotonic() + CLIENT_READ_TIMEOUT_SECONDS
        return read_pipe_message(
            handle,
            win32file=win32file,
            pywintypes=pywintypes,
            options=PipeReadOptions(
                chunk_size=PIPE_CHUNK_SIZE,
                max_bytes=MAX_MESSAGE_SIZE,
                read_chunk=functools.partial(self._read_chunk, handle, deadline),
            ),
        )

    def _read_chunk(
        self, handle: object, deadline: float, chunk_size: int
    ) -> tuple[int, bytes]:
        """Read up to *chunk_size* bytes with a cancellable overlapped read.

        Propagates :class:`PipeReadCancelled` when *deadline* expires or the
        server stops before the read completes; the pending read is cancelled
        and drained before that signal escapes.

        Returns
        -------
        tuple[int, bytes]
            The ``ReadFile`` status and the bytes transferred.
        """
        overlapped, event = self._create_overlapped()
        try:
            buffer = win32file.AllocateReadBuffer(chunk_size)
            if self._start_overlapped_read(handle, buffer, overlapped) == (
                ERROR_IO_PENDING
            ):
                self._await_read(handle, overlapped, event, deadline)
            return self._finish_overlapped_read(handle, overlapped, buffer, chunk_size)
        finally:
            win32file.CloseHandle(event)

    @staticmethod
    def _start_overlapped_read(
        handle: object, buffer: object, overlapped: object
    ) -> int:
        """Issue the overlapped ``ReadFile`` and report its initial status.

        Returns
        -------
        int
            ``ERROR_IO_PENDING`` when the read is still in flight, otherwise
            the completion status reported synchronously.
        """
        try:
            status, _ = win32file.ReadFile(handle, buffer, overlapped)
        except pywintypes.error as exc:
            if exc.winerror != ERROR_IO_PENDING:
                raise
            return ERROR_IO_PENDING
        return status

    def _await_read(
        self, handle: object, overlapped: object, event: object, deadline: float
    ) -> None:
        """Wait for the pending read, the deadline, or shutdown.

        Raises
        ------
        PipeReadCancelled
            If the deadline expires or the server stops. The read is cancelled
            and drained before the signal propagates, so the kernel is no
            longer writing into the buffer once this returns.
        """
        result = self._wait_for_overlapped(event, remaining_ms(deadline))
        if result == win32event.WAIT_OBJECT_0:
            return
        self._cancel_overlapped(handle, overlapped)
        raise PipeReadCancelled(timed_out=result == win32event.WAIT_TIMEOUT)

    @staticmethod
    def _finish_overlapped_read(
        handle: object, overlapped: object, buffer: cabc.Buffer, chunk_size: int
    ) -> tuple[int, bytes]:
        """Collect the completed overlapped read.

        Returns
        -------
        tuple[int, bytes]
            The ``ReadFile`` status and the transferred bytes.
        """
        try:
            # GetOverlappedResult(handle, overlapped, wait).
            transferred = win32file.GetOverlappedResult(
                handle,
                overlapped,
                True,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
            )
        except pywintypes.error as exc:
            if exc.winerror != ERROR_MORE_DATA:
                raise
            # ERROR_MORE_DATA means the buffer was filled and the message
            # continues; pywin32 does not surface the count alongside the
            # error, but a filled buffer is by definition ``chunk_size`` bytes.
            return ERROR_MORE_DATA, bytes(memoryview(buffer)[:chunk_size])
        return 0, bytes(memoryview(buffer)[:transferred])

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
