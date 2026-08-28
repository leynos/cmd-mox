"""Client helpers for talking to the IPC server."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import importlib
import json
import logging
import os
import random
import socket
import threading
import time
import typing as typ
from pathlib import Path

from cmd_mox import _path_utils as path_utils
from cmd_mox._validators import (
    validate_positive_finite_timeout,
    validate_retry_attempts,
    validate_retry_backoff,
    validate_retry_jitter,
)
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc.windows import (
    ERROR_FILE_NOT_FOUND,
    ERROR_PIPE_BUSY,
    PIPE_CHUNK_SIZE,
    PipeReadOptions,
    derive_pipe_name,
    read_pipe_message,
    write_pipe_payload,
)

from . import _client_events, _observability
from .constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from .json_utils import parse_json_safely
from .models import Invocation, PassthroughResult, Response

if typ.TYPE_CHECKING:
    import collections.abc as cabc

logger = logging.getLogger(__name__)

if path_utils.IS_WINDOWS:  # pragma: win32-only
    try:
        pywintypes = importlib.import_module("pywintypes")
        win32file = importlib.import_module("win32file")
        win32pipe = importlib.import_module("win32pipe")
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        msg = "pywin32 is required for Windows named pipe support"
        raise RuntimeError(msg) from exc
else:  # pragma: no cover - satisfies type-checkers on non-Windows hosts
    pywintypes = typ.cast("typ.Any", None)
    win32file = typ.cast("typ.Any", None)
    win32pipe = typ.cast("typ.Any", None)

DEFAULT_CONNECT_RETRIES: typ.Final[int] = 3
DEFAULT_CONNECT_BACKOFF: typ.Final[float] = 0.05
DEFAULT_CONNECT_JITTER: typ.Final[float] = 0.2
MIN_RETRY_SLEEP: typ.Final[float] = 0.001
IO_CANCEL_GRACE: typ.Final[float] = 0.05

_SENTINEL: typ.Final[object] = object()


@dc.dataclass(slots=True)
class RetryConfig:
    """Configuration for connection retry behavior."""

    retries: int = DEFAULT_CONNECT_RETRIES
    backoff: float = DEFAULT_CONNECT_BACKOFF
    jitter: float = DEFAULT_CONNECT_JITTER

    def __post_init__(self) -> None:
        """Validate retry configuration values."""
        validate_retry_attempts(self.retries)
        validate_retry_backoff(self.backoff)
        validate_retry_jitter(self.jitter)

    def validate(self, timeout: float) -> None:
        """Re-validate retry configuration alongside the connection timeout."""
        validate_positive_finite_timeout(timeout)
        self.__post_init__()


@dc.dataclass(slots=True)
class RetryStrategy:
    """Hooks for logging and gating retry behaviour."""

    on_failure: cabc.Callable[[int, Exception], None] | None = None
    should_retry: cabc.Callable[[Exception, int, int], bool] | None = None
    sleep: cabc.Callable[[float], None] = time.sleep


@dc.dataclass(frozen=True, slots=True)
class _ConnectionContext:
    """Per-request connection parameters shared by both client transports.

    Bundling the timeout, retry configuration, and correlation identifier keeps
    the transport helpers within the project's argument-count limit and lets the
    identifier reach the retry seam without widening any public signature.
    """

    timeout: float
    retry_config: RetryConfig
    correlation_id: str | None = None

    def validate(self) -> None:
        """Re-validate the retry configuration against the timeout."""
        self.retry_config.validate(self.timeout)


def calculate_retry_delay(attempt: int, backoff: float, jitter: float) -> float:
    """Return the sleep delay for a 0-based *attempt*.

    Never shorter than :data:`MIN_RETRY_SLEEP`.

    Returns
    -------
    float
        The bounded, optionally jittered retry delay in seconds.
    """
    delay = backoff * (attempt + 1)
    if jitter:
        # Randomise the linear backoff within the jitter bounds to avoid
        # thundering herds if many clients retry simultaneously.
        factor = random.uniform(1.0 - jitter, 1.0 + jitter)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - non-cryptographic jitter for retry backoff
        delay *= factor
    return max(delay, MIN_RETRY_SLEEP)


@dc.dataclass(slots=True)
class _RetryContext:
    attempt: int
    max_attempts: int
    retry_config: RetryConfig
    strategy: RetryStrategy


def _handle_retry_failure(
    exc: Exception,
    context: _RetryContext,
) -> float:
    """Process a failure from a retry attempt and return the backoff delay.

    Raises *exc* when no further retries should be attempted.

    Returns
    -------
    float
        The delay before the next retry attempt.
    """
    if context.strategy.on_failure is not None:
        context.strategy.on_failure(context.attempt, exc)

    retry_decider = context.strategy.should_retry or (
        lambda _exc, att, maximum: att < maximum - 1
    )
    if not retry_decider(exc, context.attempt, context.max_attempts):
        raise

    return calculate_retry_delay(
        context.attempt,
        context.retry_config.backoff,
        context.retry_config.jitter,
    )


def retry_with_backoff[T](
    func: cabc.Callable[[int], T],
    *,
    retry_config: RetryConfig,
    strategy: RetryStrategy | None = None,
) -> T:
    """Execute *func* until it succeeds or retries are exhausted.

    The callable receives the 0-based attempt index. When provided, the
    strategy's ``on_failure`` runs on every raised exception (e.g., for
    logging). The strategy's ``should_retry`` decides whether to try again,
    defaulting to retrying until the configured maximum attempts. The delay
    between attempts is calculated from ``retry_config.backoff`` and
    ``retry_config.jitter`` using :func:`calculate_retry_delay`, and the wait
    is performed via ``strategy.sleep`` (defaulting to :func:`time.sleep` when
    *strategy* is ``None``).

    Returns
    -------
    T
        The value returned by the first successful invocation of ``func``.

    Raises
    ------
    RuntimeError
        If the retry loop terminates without returning or propagating a failure.
    """
    max_attempts = retry_config.retries
    strat = strategy if strategy is not None else RetryStrategy()

    for attempt in range(max_attempts):
        try:
            return func(attempt)
        except Exception as exc:  # ruff: ignore[blind-except] - generic retry helper: *func* is caller-supplied and may raise anything
            context = _RetryContext(
                attempt=attempt,
                max_attempts=max_attempts,
                retry_config=retry_config,
                strategy=strat,
            )
            delay = _handle_retry_failure(exc, context)
            strat.sleep(delay)

    msg = (
        "Unreachable code reached in retry helper: all attempts exhausted "
        "without returning a value."
    )
    raise RuntimeError(msg)  # pragma: no cover


def _compute_deadline(timeout: float) -> float:
    """Return the absolute deadline for *timeout* seconds from now.

    Returns
    -------
    float
        The monotonic clock value at which *timeout* expires.
    """
    return time.monotonic() + timeout


def _remaining_time(deadline: float) -> float:
    """Return the seconds remaining before *deadline* expires.

    Returns
    -------
    float
        The strictly positive seconds left before *deadline*.

    Raises
    ------
    TimeoutError
        If *deadline* has already passed.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        msg = "IPC client operation timed out"
        raise TimeoutError(msg)
    return remaining


class _HandleCloser:
    """Best-effort guard that closes a Windows handle exactly once."""

    __slots__ = ("_closed", "_handle")

    def __init__(self, handle: object) -> None:
        self._handle = handle
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(pywintypes.error):
            # Double-close or already aborted handles may report INVALID_HANDLE;
            # callers only care that resources are reclaimed.
            win32file.CloseHandle(self._handle)

    @property
    def closed(self) -> bool:
        return self._closed


def _validate_initial_deadline(
    deadline: float, cancel: cabc.Callable[[], None], thread: threading.Thread
) -> float:
    """Validate the deadline and return remaining time.

    If already expired, cancel the operation and raise TimeoutError.

    Returns
    -------
    float
        The positive time remaining before the operation's deadline.

    Raises
    ------
    TimeoutError
        If the deadline has already passed.
    """
    try:
        return _remaining_time(deadline)
    except TimeoutError:
        cancel()
        thread.join(IO_CANCEL_GRACE)
        raise


def _join_with_timeout_and_cancel(
    thread: threading.Thread, remaining: float, cancel: cabc.Callable[[], None]
) -> None:
    """Join the thread with timeout; cancel and raise if still alive.

    Raises
    ------
    TimeoutError
        If *thread* is still running after *remaining* seconds.
    """
    thread.join(remaining)
    if thread.is_alive():
        cancel()
        thread.join(IO_CANCEL_GRACE)
        msg = "IPC client operation timed out"
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

    Returns
    -------
    T
        The value returned by *func*.
    """
    outcome: dict[str, typ.Any] = {"value": _SENTINEL}

    def _target() -> None:
        try:
            outcome["value"] = func()
        except BaseException as exc:  # ruff: ignore[blind-except] - worker-thread boundary: any failure must be stashed and re-raised on the caller's thread
            outcome["error"] = exc

    thread = threading.Thread(
        target=_target,
        name="cmd-mox-ipc-io",
        daemon=True,
    )
    thread.start()

    remaining = _validate_initial_deadline(deadline, cancel, thread)
    _join_with_timeout_and_cancel(thread, remaining, cancel)
    return typ.cast("T", _extract_outcome(outcome))


def _connect_unix_with_retries(
    sock_path: Path,
    context: _ConnectionContext,
) -> socket.socket:
    """Connect to *sock_path* retrying on :class:`OSError`.

    Returns
    -------
    socket.socket
        The connected Unix domain socket.
    """
    context.validate()
    address = str(sock_path)

    def attempt_connect(_attempt: int) -> socket.socket:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(context.timeout)
        try:
            sock.connect(address)
        except OSError:
            sock.close()
            raise
        return sock

    def log_failure(attempt: int, exc: Exception) -> None:
        logger.debug(
            "IPC connect attempt %d/%d to %s failed: %s",
            attempt + 1,
            context.retry_config.retries,
            address,
            exc,
        )
        _client_events.emit_connect_retry("unix", attempt, exc, context.correlation_id)

    return retry_with_backoff(
        attempt_connect,
        retry_config=context.retry_config,
        strategy=RetryStrategy(on_failure=log_failure),
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


def _read_all(sock: socket.socket) -> bytes:
    """Read all data from *sock* until EOF.

    Returns
    -------
    bytes
        Every byte received before the peer closed the connection.
    """
    chunks = []
    while chunk := sock.recv(1024):
        chunks.append(chunk)
    return b"".join(chunks)


def _send_unix_request(
    sock_path: Path,
    payload: bytes,
    context: _ConnectionContext,
) -> bytes:
    with _connect_unix_with_retries(sock_path, context) as client:
        client.sendall(payload)
        client.shutdown(socket.SHUT_WR)
        return _read_all(client)


def _decode_response(raw: bytes) -> Response:
    parsed = parse_json_safely(raw)
    if parsed is None:
        msg = "Invalid JSON from IPC server"
        raise RuntimeError(msg)
    return Response.from_payload(parsed)


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
    """Wait for *pipe_name* to become available, falling back to sleep."""
    wait_duration = delay
    if deadline is not None:
        wait_duration = min(delay, _remaining_time(deadline))
    wait_ms = max(1, int(wait_duration * 1000))
    try:
        win32pipe.WaitNamedPipe(pipe_name, wait_ms)
    except pywintypes.error:
        time.sleep(wait_duration)


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
    context.validate()
    pipe_name_str = os.fspath(pipe_name)
    connect_deadline = deadline or _compute_deadline(context.timeout)

    def log_failure(attempt: int, exc: Exception) -> None:
        logger.debug(
            "IPC pipe connect attempt %d/%d to %s failed: %s",
            attempt + 1,
            context.retry_config.retries,
            pipe_name,
            exc,
        )
        _client_events.emit_connect_retry(
            "named_pipe", attempt, exc, context.correlation_id
        )

    def sleep(delay: float) -> None:
        _wait_for_pipe_availability(
            pipe_name_str,
            delay,
            deadline=connect_deadline,
        )

    return retry_with_backoff(
        lambda _attempt: _create_pipe_handle(pipe_name_str),
        retry_config=context.retry_config,
        strategy=RetryStrategy(
            on_failure=log_failure,
            should_retry=_should_retry_pipe_error,
            sleep=sleep,
        ),
    )


def _send_pipe_request(
    sock_path: Path,
    payload: bytes,
    context: _ConnectionContext,
) -> bytes:
    pipe_name = derive_pipe_name(sock_path)
    timeout = context.timeout
    connect_deadline = _compute_deadline(timeout)
    handle = _connect_pipe_with_retries(
        pipe_name,
        context,
        deadline=connect_deadline,
    )
    closer = _HandleCloser(handle)
    try:
        _run_blocking_io(
            lambda: write_pipe_payload(
                handle,
                payload,
                win32file=win32file,
            ),
            deadline=_compute_deadline(timeout),
            cancel=closer.close,
        )
        return _run_blocking_io(
            lambda: read_pipe_message(
                handle,
                win32file=win32file,
                pywintypes=pywintypes,
                options=PipeReadOptions(chunk_size=PIPE_CHUNK_SIZE),
            ),
            deadline=_compute_deadline(timeout),
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
    timeout: float,
    retry_config: RetryConfig | None,
) -> Response:
    """Send a JSON request of *kind* to the IPC server.

    Returns
    -------
    Response
        The decoded server response.
    """
    payload_bytes, correlation_id = _build_request_envelope(kind, data)
    context = _ConnectionContext(
        timeout=timeout,
        retry_config=retry_config or RetryConfig(),
        correlation_id=correlation_id,
    )
    return _perform_request(payload_bytes, kind, context)


def invoke_server(
    invocation: Invocation,
    timeout: float,
    retry_config: RetryConfig | None = None,
) -> Response:
    """Send *invocation* to the IPC server and return its response.

    The *timeout* applies to each blocking connect/send/receive operation.
    Unix clients rely on ``socket.settimeout`` so the kernel enforces the
    limit, while Windows clients cooperatively track the deadline and close
    the named pipe if any step exceeds *timeout*, raising ``TimeoutError``.

    Returns
    -------
    Response
        The IPC server's response to the invocation.
    """
    return _send_request(KIND_INVOCATION, invocation.to_dict(), timeout, retry_config)


def report_passthrough_result(
    result: PassthroughResult,
    timeout: float,
    retry_config: RetryConfig | None = None,
) -> Response:
    """Send passthrough execution results back to the IPC server.

    Timeout handling mirrors :func:`invoke_server`: Unix sockets enforce the
    limit per system call, and Windows callers rely on cooperative deadlines
    that cancel the named pipe when *timeout* expires.

    Returns
    -------
    Response
        The IPC server's acknowledgement of the passthrough result.
    """
    return _send_request(
        KIND_PASSTHROUGH_RESULT,
        result.to_dict(),
        timeout,
        retry_config,
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
