"""Client helpers for talking to the IPC server."""

from __future__ import annotations

import dataclasses as dc
import json
import os
import socket
import threading
import time
import typing as typ
from functools import partial
from pathlib import Path

from cmd_mox import _bounded_thread
from cmd_mox import _path_utils as path_utils
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc.windows import (
    ERROR_FILE_NOT_FOUND,
    ERROR_PIPE_BUSY,
    PIPE_CHUNK_SIZE,
    PipeReadOptions,
    derive_pipe_name,
    pywintypes,
    read_pipe_message,
    win32api,
    win32file,
    win32pipe,
    write_pipe_payload,
)

from . import _client_events, _observability, wire
from ._deadline import compute_deadline, remaining, remaining_or_none, timeout_ms
from ._retry import (
    DEFAULT_CONNECT_BACKOFF,
    DEFAULT_CONNECT_JITTER,
    DEFAULT_CONNECT_RETRIES,
    MIN_RETRY_SLEEP,
    RetryConfig,
    RetryStrategy,
    calculate_retry_delay,
    retry_with_backoff,
)
from .constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from .json_utils import parse_json_safely

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from cmd_mox.domain import Invocation, PassthroughResult, Response

# Upper bound on the wait for a cancelled I/O worker to unwind: generous enough
# for a cancellation to complete, short enough that a wedged worker cannot hang
# the caller indefinitely.
IO_CANCEL_GRACE: typ.Final[float] = 1.0

_SENTINEL: typ.Final[object] = object()


@dc.dataclass(frozen=True, slots=True)
class _ConnectionContext:
    """Transport settings and metadata shared throughout one client request."""

    timeout: float
    retry_config: RetryConfig
    correlation_id: str | None = None
    deadline: float | None = None

    def validate(self) -> None:
        """Re-validate the retry configuration against the timeout."""
        self.retry_config.validate(self.timeout)


def _pipe_handle_closer(handle: object) -> _bounded_thread.HandleCloser:
    """Return a close-once guard for a named-pipe *handle*.

    ``win32file`` and ``pywintypes.error`` are both bound here, while building
    the guard; only ``CloseHandle`` itself is looked up when the handle is
    closed. Callers therefore reach this helper only on a host where pywin32
    is importable, which the Windows transport already guarantees.

    Returns
    -------
    HandleCloser
        Guard that closes *handle* at most once.
    """
    return _bounded_thread.HandleCloser(
        handle,
        win32file=win32file,
        suppress=(pywintypes.error,),
    )


def _request_synchronous_io_cancel(thread: threading.Thread) -> None:
    """Ask Windows to abort *thread*'s in-flight synchronous I/O."""
    # Closing the pipe handle does not reliably wake a thread already blocked
    # inside ReadFile; CancelSynchronousIo does. The pywin32 modules are read
    # from module globals here so the helper honours test and host stand-ins.
    _bounded_thread.request_synchronous_io_cancel(
        thread, win32file=win32file, win32api=win32api, pywintypes=pywintypes
    )


def _cancel_and_await_worker(
    thread: threading.Thread, cancel: cabc.Callable[[], None]
) -> bool:
    """Abort the worker's I/O and wait, bounded, for the worker to exit.

    Returning before the worker has exited would leave a daemon thread owning
    the pipe handle the caller believes it has released, so the wait is not
    optional; it is merely bounded by :data:`IO_CANCEL_GRACE`.

    Returns
    -------
    bool
        Whether the worker exited before the grace period elapsed.
    """
    return _bounded_thread.cancel_and_await_worker(
        thread,
        cancel,
        _bounded_thread.CancelPolicy(
            grace=IO_CANCEL_GRACE,
            request_cancel=_request_synchronous_io_cancel,
        ),
    )


def _join_with_timeout_and_cancel(
    thread: threading.Thread, budget: float, cancel: cabc.Callable[[], None]
) -> None:
    """Join the thread with timeout; cancel and raise if still alive.

    The timeout is not reported until the cancelled worker has terminated, so
    the caller never resumes while a daemon thread still owns the pipe handle.
    A worker surviving the bounded grace is named in the raised message.

    Raises
    ------
    TimeoutError
        If *thread* is still running after *budget* seconds.
    """
    thread.join(budget)
    if not thread.is_alive():
        return
    msg = "IPC client operation timed out"
    if not _cancel_and_await_worker(thread, cancel):
        msg += "; the I/O worker did not exit after cancellation"
    raise TimeoutError(msg)


def _extract_outcome(outcome: dict[str, typ.Any]) -> object:
    """Extract the result from the outcome dict, raising any stored error.

    Returns
    -------
    object
        The worker thread's return value, or ``None`` when it produced none.

    Raises
    ------
    BaseException
        The exception captured by the worker thread, re-raised on the caller's
        thread.
    """  # ruff: ignore[docstring-extraneous-exception] - the stored worker-thread exception is re-raised verbatim and stays caller-visible
    if (error := outcome.get("error")) is not None:
        raise error
    value = outcome.get("value", _SENTINEL)
    if value is _SENTINEL:
        value = None
    return value


def _run_blocking_io[T](
    func: cabc.Callable[[], T],
    *,
    deadline: float,
    cancel: cabc.Callable[[], None],
) -> T:
    """Execute *func* on a worker thread until completion or timeout.

    The shared deadline is checked before the worker starts. An expired
    request therefore fails without creating a thread to cancel, and the
    cancellation grace below only ever applies to a worker that exists.

    The budget is re-read after the worker starts, because constructing and
    starting the thread spends part of the same deadline. A budget that runs
    out during startup still reaches the cancel-and-join path: that is the
    only path which waits for the worker to release the pipe handle.

    The worker re-checks the deadline before running *func*, because
    scheduling can hold the thread until after the caller has given up. That
    check narrows the window rather than closing it: a deadline that expires
    between the check and the first I/O call still lets that call start, and
    the cancel-and-join path bounds what follows. The check runs inside the
    outcome guard, so a worker that finds the budget spent re-raises its
    ``TimeoutError`` on the caller's thread instead of returning ``None``.

    Returns
    -------
    T
        The value returned by *func*.
    """
    remaining(deadline)
    outcome: dict[str, typ.Any] = {"value": _SENTINEL}

    def run_once_started() -> T:
        """Run *func* only while the shared budget remains."""
        remaining(deadline)
        return func()

    thread = threading.Thread(
        target=_bounded_thread.stash_outcome(outcome, run_once_started),
        name="cmd-mox-ipc-io",
        daemon=True,
    )
    thread.start()

    left = remaining_or_none(deadline)
    _join_with_timeout_and_cancel(thread, 0.0 if left is None else left, cancel)
    return typ.cast("T", _extract_outcome(outcome))


def _log_connect_retry(
    transport: _observability.Transport,
    attempt: int,
    exc: Exception,
    context: _ConnectionContext,
) -> None:
    """Emit the bounded connect-retry event for *transport*.

    The bounded event is the only report: the endpoint and the exception
    message are both on the observability never-log list.
    """
    _client_events.emit_connect_retry(transport, attempt, exc, context.correlation_id)


def _connect_unix_with_retries(
    sock_path: Path,
    context: _ConnectionContext,
    *,
    deadline: float | None = None,
) -> socket.socket:
    """Connect to *sock_path* retrying on :class:`OSError`.

    Returns
    -------
    socket.socket
        The connected Unix domain socket.
    """
    address = str(sock_path)
    request_deadline = deadline if deadline is not None else context.deadline
    if request_deadline is None:
        request_deadline = compute_deadline(context.timeout)
    remaining(request_deadline)

    def attempt_connect(_attempt: int) -> socket.socket:
        """Open a fresh Unix socket and connect it, closing it if connect fails."""
        remaining(request_deadline)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(remaining(request_deadline))
            sock.connect(address)
        except OSError:
            sock.close()
            raise
        return sock

    def bounded_sleep(delay: float) -> None:
        """Limit retry backoff to the remaining request deadline."""
        time.sleep(min(delay, remaining(request_deadline)))

    return retry_with_backoff(
        attempt_connect,
        retry_config=context.retry_config,
        strategy=RetryStrategy(
            on_failure=partial(_log_connect_retry, "unix", context=context),
            sleep=bounded_sleep,
        ),
    )


def _get_validated_socket_path() -> Path:
    """Fetch the IPC socket path from the environment.

    Returns
    -------
    Path
        The configured IPC socket path.

    Raises
    ------
    RuntimeError
        If the socket path environment variable is unset.
    """
    sock = os.environ.get(CMOX_IPC_SOCKET_ENV)
    if sock is None:
        msg = f"{CMOX_IPC_SOCKET_ENV} is not set"
        raise RuntimeError(msg)
    return Path(sock)


def _read_all(sock: socket.socket, deadline: float) -> bytes:
    """Read all data from *sock* until EOF.

    Returns
    -------
    bytes
        Every byte received before the peer closed the connection.
    """
    chunks = []
    while True:
        sock.settimeout(remaining(deadline))
        chunk = sock.recv(1024)
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


def _send_unix_request(
    sock_path: Path,
    payload: bytes,
    context: _ConnectionContext,
) -> bytes:
    """Send one payload over a retried Unix connection and return the full reply."""
    context.validate()
    deadline = context.deadline
    if deadline is None:
        deadline = compute_deadline(context.timeout)
    remaining(deadline)
    with _connect_unix_with_retries(sock_path, context, deadline=deadline) as client:
        client.settimeout(remaining(deadline))
        client.sendall(payload)
        client.settimeout(remaining(deadline))
        client.shutdown(socket.SHUT_WR)
        return _read_all(client, deadline)


def _decode_response(raw: bytes) -> Response:
    """Parse raw reply bytes into a Response, rejecting invalid JSON."""
    parsed = parse_json_safely(raw)
    if parsed is None:
        msg = "Invalid JSON from IPC server"
        raise RuntimeError(msg)
    return wire.decode_response(parsed)


def _should_retry_pipe_error(exc: object, attempt: int, max_retries: int) -> bool:
    """Return True when *exc* represents a retryable pipe error.

    Returns
    -------
    bool
        ``True`` when *exc* is a busy/not-found pipe error and attempts remain.
    """
    if getattr(exc, "winerror", None) not in {ERROR_PIPE_BUSY, ERROR_FILE_NOT_FOUND}:
        return False
    return attempt < max_retries - 1


def _wait_for_pipe_availability(
    pipe_name: str,
    delay: float,
    *,
    deadline: float | None = None,
) -> None:
    """Wait for *pipe_name* to become available, falling back to sleep.

    Both the Win32 wait and the fallback sleep are limited by whatever budget
    the shared *deadline* has left, and the remaining budget is re-read after
    the wait so a slow ``WaitNamedPipe`` cannot overshoot it.

    Raises
    ------
    TimeoutError
        The shared deadline expired before the pipe became available.
    """  # ruff: ignore[docstring-extraneous-exception] - the shared budget raises it, not this helper

    def bounded_wait(seconds: float) -> float:
        """Clamp *seconds* to the remaining shared budget."""
        return seconds if deadline is None else min(seconds, remaining(deadline))

    wait_ms = timeout_ms(bounded_wait(delay))
    try:
        win32pipe.WaitNamedPipe(pipe_name, wait_ms)
    except pywintypes.error:
        time.sleep(bounded_wait(delay))


def _create_pipe_handle(pipe_name: str) -> object:
    """Create and configure a handle for *pipe_name*.

    Returns
    -------
    object
        The opaque pywin32 handle, switched to message read mode.
    """
    handle = win32file.CreateFile(
        pipe_name,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,
        0,
        None,
        win32file.OPEN_EXISTING,
        0,
        None,
    )
    win32pipe.SetNamedPipeHandleState(
        handle,
        getattr(win32pipe, "PIPE_READMODE_MESSAGE", 2),
        None,
        None,
    )
    return handle


def _connect_pipe_with_retries(
    pipe_name: os.PathLike[str] | str,
    context: _ConnectionContext,
    *,
    deadline: float | None = None,
) -> object:
    """Open a handle to the named pipe, retrying while it is busy or missing.

    An omitted *deadline* falls back to the context's shared deadline and then
    to one computed from the context timeout. The chosen deadline bounds every
    attempt, so retries can never extend the caller's budget.

    Returns
    -------
    object
        The opaque pywin32 handle to the connected pipe.
    """
    pipe_name_str = os.fspath(pipe_name)
    connect_deadline = deadline if deadline is not None else context.deadline
    if connect_deadline is None:
        connect_deadline = compute_deadline(context.timeout)
    remaining(connect_deadline)

    def open_handle(_attempt: int) -> object:
        """Open the pipe only while the shared connect budget remains."""
        remaining(connect_deadline)
        return _create_pipe_handle(pipe_name_str)

    def sleep(delay: float) -> None:
        """Wait for the pipe to become available, bounded by the connect deadline."""
        _wait_for_pipe_availability(
            pipe_name_str,
            delay,
            deadline=connect_deadline,
        )

    return retry_with_backoff(
        open_handle,
        retry_config=context.retry_config,
        strategy=RetryStrategy(
            on_failure=partial(_log_connect_retry, "named_pipe", context=context),
            should_retry=_should_retry_pipe_error,
            sleep=sleep,
        ),
    )


def _send_pipe_request(
    sock_path: Path,
    payload: bytes,
    context: _ConnectionContext,
) -> bytes:
    """Send one payload over a named pipe and return the full reply.

    The pipe transport shares a single absolute deadline with the Unix
    transport: connecting, writing the request, and reading the reply all draw
    down the same budget. A caller that supplies no deadline grants the context
    timeout from now, measured after the payload was encoded.

    A reply that expires may report its timeout up to the cancellation grace
    later, because the overlapped worker is cancelled and drained before the
    handle is released.

    Returns
    -------
    bytes
        Every byte of the server's reply.
    """
    pipe_name = derive_pipe_name(sock_path)
    deadline = context.deadline
    if deadline is None:
        deadline = compute_deadline(context.timeout)
    handle = _connect_pipe_with_retries(
        pipe_name,
        context,
        deadline=deadline,
    )
    closer = _pipe_handle_closer(handle)
    try:
        _run_blocking_io(
            lambda: write_pipe_payload(
                handle,
                payload,
                win32file=win32file,
            ),
            deadline=deadline,
            cancel=closer.close,
        )
        return _run_blocking_io(
            lambda: read_pipe_message(
                handle,
                win32file=win32file,
                pywintypes=pywintypes,
                options=PipeReadOptions(chunk_size=PIPE_CHUNK_SIZE),
            ),
            deadline=deadline,
            cancel=closer.close,
        )
    finally:
        closer.close()


def _build_request_envelope(kind: str, data: dict[str, typ.Any]) -> tuple[bytes, str]:
    """Encode *data* as a request envelope of *kind*.

    The envelope adds two fields alongside the model's own: ``kind`` and the
    opaque ``correlation_id``. Both are stripped by the server before the body
    is validated.

    Returns
    -------
    tuple[bytes, str]
        The encoded envelope and its correlation identifier.
    """
    correlation_id = _client_events.resolve_correlation_id(data)
    payload = dict(data)
    payload["kind"] = kind
    payload["correlation_id"] = correlation_id
    return json.dumps(payload).encode("utf-8"), correlation_id


def _dispatch_request(payload: bytes, context: _ConnectionContext) -> bytes:
    """Send *payload* over the transport this host uses.

    Returns
    -------
    bytes
        The raw response bytes returned by the server.
    """
    # Validate once at the transport boundary rather than in each transport
    # helper, so a request cannot reach either retry loop unvalidated.
    context.validate()
    sock_path = _get_validated_socket_path()
    if path_utils.IS_WINDOWS:
        return _send_pipe_request(sock_path, payload, context)
    return _send_unix_request(sock_path, payload, context)


def _perform_request(
    payload: bytes, kind: str, context: _ConnectionContext
) -> Response:
    """Send *payload*, emitting bounded request-lifecycle events.

    Returns
    -------
    Response
        The decoded server response.
    """
    started = time.perf_counter()
    _client_events.RequestEvent(
        kind=kind,
        outcome="started",
        correlation_id=context.correlation_id,
        message_size=len(payload),
    ).emit()
    try:
        response = _decode_response(_dispatch_request(payload, context))
    except Exception as exc:
        _client_events.RequestEvent(
            kind=kind,
            outcome="error",
            correlation_id=context.correlation_id,
            duration_ms=_observability.elapsed_ms(started),
            error_category=type(exc).__name__,
        ).emit()
        raise
    _client_events.RequestEvent(
        kind=kind,
        outcome="success",
        correlation_id=context.correlation_id,
        duration_ms=_observability.elapsed_ms(started),
    ).emit()
    return response


def _send_request(
    kind: str,
    data: dict[str, typ.Any],
    context: _ConnectionContext,
) -> Response:
    """Send a JSON request of *kind* to the IPC server.

    Returns
    -------
    Response
    """
    payload_bytes, correlation_id = _build_request_envelope(kind, data)
    context = dc.replace(context, correlation_id=correlation_id)
    return _perform_request(payload_bytes, kind, context)


def invoke_server(
    invocation: Invocation,
    timeout: float,
    retry_config: RetryConfig | None = None,
    *,
    deadline: float | None = None,
) -> Response:
    """Send *invocation* to the IPC server and return its response.

    Both transports share one absolute deadline across connecting, writing the
    request, and reading the reply, so retries and connection attempts cannot
    extend the caller's budget. On Windows an expired request may take up to a
    further second to cancel and drain its overlapped I/O before the timeout is
    reported.

    Parameters
    ----------
    invocation : Invocation
        Request to send.
    timeout : float
        Request limit in seconds, used when *deadline* is omitted.
    retry_config : RetryConfig or None, optional
        Connection retry policy.
    deadline : float or None, optional
        Absolute monotonic deadline shared with preceding shim work, such as
        capturing stdin. ``None`` grants *timeout* from now.

    Returns
    -------
    Response
        The IPC server's response to the invocation.
    """
    return _send_request(
        KIND_INVOCATION,
        wire.invocation_to_payload(invocation),
        _ConnectionContext(timeout, retry_config or RetryConfig(), deadline=deadline),
    )


def report_passthrough_result(
    result: PassthroughResult,
    timeout: float,
    retry_config: RetryConfig | None = None,
    *,
    deadline: float | None = None,
) -> Response:
    """Send passthrough execution results back to the IPC server.

    Both transports share one absolute deadline across connecting, writing the
    report, and reading the acknowledgement. On Windows an expired report may
    take up to a further second to cancel and drain its overlapped I/O before
    the timeout is reported.

    Parameters
    ----------
    result : PassthroughResult
        Command result to report.
    timeout : float
        Request limit in seconds, used when *deadline* is omitted.
    retry_config : RetryConfig or None, optional
        Connection retry policy.
    deadline : float or None, optional
        Absolute monotonic deadline shared with the passthrough command and
        the preceding request. ``None`` grants *timeout* from now.

    Returns
    -------
    Response
        The IPC server's acknowledgement of the passthrough result.
    """
    return _send_request(
        KIND_PASSTHROUGH_RESULT,
        wire.passthrough_to_payload(result),
        _ConnectionContext(timeout, retry_config or RetryConfig(), deadline=deadline),
    )


__all__ = [
    "DEFAULT_CONNECT_BACKOFF",
    "DEFAULT_CONNECT_JITTER",
    "DEFAULT_CONNECT_RETRIES",
    "MIN_RETRY_SLEEP",
    "RetryConfig",
    "RetryStrategy",
    "calculate_retry_delay",
    "invoke_server",
    "report_passthrough_result",
    "retry_with_backoff",
]
