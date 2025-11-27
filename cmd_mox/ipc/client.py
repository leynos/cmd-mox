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
import typing as t
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
    ERROR_BROKEN_PIPE,
    ERROR_FILE_NOT_FOUND,
    ERROR_MORE_DATA,
    ERROR_PIPE_BUSY,
    PIPE_CHUNK_SIZE,
    read_pipe_message,
    write_pipe_message,
    derive_pipe_name,
)

from .constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from .json_utils import parse_json_safely
from .models import Invocation, PassthroughResult, Response

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
    pywintypes = t.cast("t.Any", None)
    win32file = t.cast("t.Any", None)
    win32pipe = t.cast("t.Any", None)

DEFAULT_CONNECT_RETRIES: t.Final[int] = 3
DEFAULT_CONNECT_BACKOFF: t.Final[float] = 0.05
DEFAULT_CONNECT_JITTER: t.Final[float] = 0.2
MIN_RETRY_SLEEP: t.Final[float] = 0.001
IO_CANCEL_GRACE: t.Final[float] = 0.05

_T = t.TypeVar("_T")
_SENTINEL: t.Final[object] = object()


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


def calculate_retry_delay(attempt: int, backoff: float, jitter: float) -> float:
    """Return the sleep delay for a 0-based *attempt*.

    Never shorter than :data:`MIN_RETRY_SLEEP`.
    """
    delay = backoff * (attempt + 1)
    if jitter:
        # Randomise the linear backoff within the jitter bounds to avoid
        # thundering herds if many clients retry simultaneously.
        factor = random.uniform(1.0 - jitter, 1.0 + jitter)  # noqa: S311
        delay *= factor
    return max(delay, MIN_RETRY_SLEEP)


def _compute_deadline(timeout: float) -> float:
    """Return the absolute deadline for *timeout* seconds from now."""
    return time.monotonic() + timeout


def _remaining_time(deadline: float) -> float:
    """Return the seconds remaining before *deadline* expires."""
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
        with contextlib.suppress(pywintypes.error):  # type: ignore[name-defined]
            # Double-close or already aborted handles may report INVALID_HANDLE;
            # callers only care that resources are reclaimed.
            win32file.CloseHandle(self._handle)  # type: ignore[union-attr]

    @property
    def closed(self) -> bool:
        return self._closed


def _validate_initial_deadline(
    deadline: float, cancel: t.Callable[[], None], thread: threading.Thread
) -> float:
    """Validate the deadline and return remaining time.

    If already expired, cancel the operation and raise TimeoutError.
    """
    try:
        return _remaining_time(deadline)
    except TimeoutError:
        cancel()
        thread.join(IO_CANCEL_GRACE)
        raise


def _join_with_timeout_and_cancel(
    thread: threading.Thread, remaining: float, cancel: t.Callable[[], None]
) -> None:
    """Join the thread with timeout; cancel and raise if still alive."""
    thread.join(remaining)
    if thread.is_alive():
        cancel()
        thread.join(IO_CANCEL_GRACE)
        msg = "IPC client operation timed out"
        raise TimeoutError(msg)


def _extract_outcome(outcome: dict[str, t.Any]) -> object:
    """Extract the result from the outcome dict, raising any stored error."""
    if (error := outcome.get("error")) is not None:
        raise t.cast("BaseException", error)
    value = outcome.get("value", _SENTINEL)
    if value is _SENTINEL:
        value = None
    return value


def _run_blocking_io(
    func: t.Callable[[], _T],
    *,
    deadline: float,
    cancel: t.Callable[[], None],
) -> _T:
    """Execute *func* on a worker thread until completion or timeout."""
    outcome: dict[str, t.Any] = {"value": _SENTINEL}

    def _target() -> None:
        try:
            outcome["value"] = func()
        except BaseException as exc:  # noqa: BLE001 - propagate cross-thread errors
            outcome["error"] = exc

    thread = threading.Thread(
        target=_target,
        name="cmd-mox-ipc-io",
        daemon=True,
    )
    thread.start()

    remaining = _validate_initial_deadline(deadline, cancel, thread)
    _join_with_timeout_and_cancel(thread, remaining, cancel)
    return t.cast("_T", _extract_outcome(outcome))


def _connect_unix_with_retries(
    sock_path: Path,
    timeout: float,
    retry_config: RetryConfig,
) -> socket.socket:
    """Connect to *sock_path* retrying on :class:`OSError`."""
    retry_config.validate(timeout)
    address = str(sock_path)
    for attempt in range(retry_config.retries):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(address)
        except OSError as exc:
            logger.debug(
                "IPC connect attempt %d/%d to %s failed: %s",
                attempt + 1,
                retry_config.retries,
                address,
                exc,
            )
            sock.close()
            if attempt < retry_config.retries - 1:
                delay = calculate_retry_delay(
                    attempt, retry_config.backoff, retry_config.jitter
                )
                time.sleep(delay)
                continue
            raise
        else:
            return sock
    msg = (
        "Unreachable code reached in socket connection loop: all retry "
        "attempts exhausted and no socket returned. This indicates a logic "
        "error or unexpected control flow."
    )
    raise RuntimeError(msg)  # pragma: no cover


def _get_validated_socket_path() -> Path:
    """Fetch the IPC socket path from the environment."""
    sock = os.environ.get(CMOX_IPC_SOCKET_ENV)
    if sock is None:
        msg = f"{CMOX_IPC_SOCKET_ENV} is not set"
        raise RuntimeError(msg)
    return Path(sock)


def _read_all(sock: socket.socket) -> bytes:
    """Read all data from *sock* until EOF."""
    chunks = []
    while chunk := sock.recv(1024):
        chunks.append(chunk)
    return b"".join(chunks)


def _send_unix_request(
    sock_path: Path,
    payload: bytes,
    timeout: float,
    retry_config: RetryConfig,
) -> bytes:
    with _connect_unix_with_retries(sock_path, timeout, retry_config) as client:
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
    """Return True when *exc* represents a retryable pipe error."""
    winerror = getattr(exc, "winerror", None)
    if winerror not in (ERROR_PIPE_BUSY, ERROR_FILE_NOT_FOUND):
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
        win32pipe.WaitNamedPipe(pipe_name, wait_ms)  # type: ignore[union-attr]
    except pywintypes.error:  # type: ignore[name-defined]
        time.sleep(wait_duration)


def _create_pipe_handle(pipe_name: str) -> object:
    """Create and configure a handle for *pipe_name*."""
    handle = win32file.CreateFile(  # type: ignore[union-attr]
        pipe_name,
        win32file.GENERIC_READ | win32file.GENERIC_WRITE,  # type: ignore[union-attr]
        0,
        None,
        win32file.OPEN_EXISTING,  # type: ignore[union-attr]
        0,
        None,
    )
    win32pipe.SetNamedPipeHandleState(  # type: ignore[union-attr]
        handle,
        t.cast("int", getattr(win32pipe, "PIPE_READMODE_MESSAGE", 2)),
        None,
        None,
    )
    return handle


def _connect_pipe_with_retries(
    pipe_name: os.PathLike[str] | str,
    timeout: float,
    retry_config: RetryConfig,
    *,
    deadline: float | None = None,
) -> object:
    retry_config.validate(timeout)
    pipe_name_str = os.fspath(pipe_name)
    connect_deadline = deadline or _compute_deadline(timeout)
    for attempt in range(retry_config.retries):
        try:
            return _create_pipe_handle(pipe_name_str)
        except pywintypes.error as exc:  # type: ignore[name-defined]
            logger.debug(
                "IPC pipe connect attempt %d/%d to %s failed: %s",
                attempt + 1,
                retry_config.retries,
                pipe_name,
                exc,
            )
            if not _should_retry_pipe_error(exc, attempt, retry_config.retries):
                raise
            delay = calculate_retry_delay(
                attempt, retry_config.backoff, retry_config.jitter
            )
            _wait_for_pipe_availability(
                pipe_name_str,
                delay,
                deadline=connect_deadline,
            )
    msg = "Exhausted retries connecting to named pipe"
    raise RuntimeError(msg)


def _read_pipe_response(handle: object) -> bytes:
    return read_pipe_message(handle)


def _send_pipe_request(
    sock_path: Path,
    payload: bytes,
    timeout: float,
    retry_config: RetryConfig,
) -> bytes:
    pipe_name = derive_pipe_name(sock_path)
    connect_deadline = _compute_deadline(timeout)
    handle = _connect_pipe_with_retries(
        pipe_name,
        timeout,
        retry_config,
        deadline=connect_deadline,
    )
    closer = _HandleCloser(handle)
    try:
        _run_blocking_io(
            lambda: _write_pipe_payload(handle, payload),
            deadline=_compute_deadline(timeout),
            cancel=closer.close,
        )
        return _run_blocking_io(
            lambda: _read_pipe_response(handle),
            deadline=_compute_deadline(timeout),
            cancel=closer.close,
        )
    finally:
        closer.close()


def _send_request(
    kind: str,
    data: dict[str, t.Any],
    timeout: float,
    retry_config: RetryConfig | None,
) -> Response:
    """Send a JSON request of *kind* to the IPC server."""
    retry = retry_config or RetryConfig()
    sock_path = _get_validated_socket_path()
    payload = dict(data)
    payload["kind"] = kind
    payload_bytes = json.dumps(payload).encode("utf-8")
    if path_utils.IS_WINDOWS:
        raw = _send_pipe_request(sock_path, payload_bytes, timeout, retry)
    else:
        raw = _send_unix_request(sock_path, payload_bytes, timeout, retry)
    return _decode_response(raw)


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
    "calculate_retry_delay",
    "invoke_server",
    "report_passthrough_result",
]
