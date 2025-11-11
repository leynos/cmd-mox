"""Client helpers for talking to the IPC server."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import json
import logging
import os
import random
import socket
import time
import typing as t
from pathlib import Path

from cmd_mox._validators import (
    validate_positive_finite_timeout,
    validate_retry_attempts,
    validate_retry_backoff,
    validate_retry_jitter,
)
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV

from .constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from .json_utils import parse_json_safely
from .models import Invocation, PassthroughResult, Response

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_RETRIES: t.Final[int] = 3
DEFAULT_CONNECT_BACKOFF: t.Final[float] = 0.05
DEFAULT_CONNECT_JITTER: t.Final[float] = 0.2
MIN_RETRY_SLEEP: t.Final[float] = 0.001
IS_WINDOWS = os.name == "nt"

try:  # pragma: no cover - pywin32 unavailable on non-Windows hosts
    if IS_WINDOWS:
        import pywintypes  # type: ignore[attr-defined]
        import win32file  # type: ignore[attr-defined]
        import win32pipe  # type: ignore[attr-defined]
        import winerror  # type: ignore[attr-defined]
    else:
        pywintypes = None  # type: ignore[assignment]
        win32file = None  # type: ignore[assignment]
        win32pipe = None  # type: ignore[assignment]
        winerror = None  # type: ignore[assignment]
except ModuleNotFoundError:  # pragma: no cover - handled dynamically
    pywintypes = None  # type: ignore[assignment]
    win32file = None  # type: ignore[assignment]
    win32pipe = None  # type: ignore[assignment]
    winerror = None  # type: ignore[assignment]

_HAS_WINDOWS_PIPES = (
    pywintypes is not None and win32file is not None and win32pipe is not None
)
_PIPE_READ_SIZE: t.Final[int] = 64 * 1024

if t.TYPE_CHECKING:  # pragma: no cover
    PipeHandle = object
else:  # pragma: no cover - runtime fallback only
    PipeHandle = object


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
    """Return the sleep delay for a 0-based *attempt*; never shorter than
    :data:`MIN_RETRY_SLEEP`.
    """
    delay = backoff * (attempt + 1)
    if jitter:
        # Randomise the linear backoff within the jitter bounds to avoid
        # thundering herds if many clients retry simultaneously.
        factor = random.uniform(1.0 - jitter, 1.0 + jitter)  # noqa: S311
        delay *= factor
    return max(delay, MIN_RETRY_SLEEP)


def _connect_with_retries(
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


def _connect_pipe_with_retries(
    pipe_name: str,
    timeout: float,
    retry_config: RetryConfig,
) -> PipeHandle:
    """Connect to *pipe_name* retrying on transient Windows errors."""
    if not _HAS_WINDOWS_PIPES:  # pragma: no cover - exercised on Windows only
        msg = "pywin32 is required for Windows named pipe IPC"
        raise RuntimeError(msg)

    retry_config.validate(timeout)
    if win32file is None or win32pipe is None or winerror is None:  # pragma: no cover
        msg = "Named pipe support is unavailable on this platform"
        raise RuntimeError(msg)

    last_error: BaseException | None = None
    for attempt in range(retry_config.retries):
        try:
            handle = win32file.CreateFile(
                pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
        except pywintypes.error as exc:  # type: ignore[union-attr]
            last_error = exc
            retriable = exc.winerror in (
                winerror.ERROR_PIPE_BUSY,
                winerror.ERROR_FILE_NOT_FOUND,
            )
            if attempt == retry_config.retries - 1 or not retriable:
                raise
            wait_ms = int(max(1.0, timeout * 1000))
            with contextlib.suppress(pywintypes.error):  # type: ignore[union-attr]
                win32pipe.WaitNamedPipe(pipe_name, wait_ms)
            delay = calculate_retry_delay(
                attempt,
                retry_config.backoff,
                retry_config.jitter,
            )
            time.sleep(delay)
            continue
        else:
            win32pipe.SetNamedPipeHandleState(  # type: ignore[union-attr]
                handle,
                win32pipe.PIPE_READMODE_MESSAGE,
                None,
                None,
            )
            return handle

    if last_error is not None:  # pragma: no cover - defensive
        raise last_error
    msg = "Named pipe connection attempts exhausted"
    raise RuntimeError(msg)


def _read_from_pipe(handle: PipeHandle) -> bytes:
    """Read all data from a named pipe handle until EOF."""
    if win32file is None or winerror is None:  # pragma: no cover - Windows only
        msg = "Named pipe support is unavailable on this platform"
        raise RuntimeError(msg)
    chunks: list[bytes] = []
    while True:
        try:
            hr, data = win32file.ReadFile(handle, _PIPE_READ_SIZE)
        except pywintypes.error as exc:  # type: ignore[union-attr]
            if exc.winerror in {winerror.ERROR_BROKEN_PIPE, winerror.ERROR_NO_DATA}:
                break
            raise
        chunks.append(data)
        if hr == 0:
            break
        if hr == winerror.ERROR_MORE_DATA:
            continue
        raise pywintypes.error(hr, "ReadFile", "Named pipe read failed")  # type: ignore[arg-type]
    return b"".join(chunks)


def _get_validated_socket_path() -> Path:
    """Fetch the IPC socket path from the environment."""
    sock = os.environ.get(CMOX_IPC_SOCKET_ENV)
    if sock is None:
        msg = f"{CMOX_IPC_SOCKET_ENV} is not set"
        raise RuntimeError(msg)
    return Path(sock)


def _is_windows_pipe_path(path: Path) -> bool:
    """Return True when *path* refers to a Windows named pipe."""
    return IS_WINDOWS and str(path).startswith("\\\\.\\pipe\\")


def _read_all(sock: socket.socket) -> bytes:
    """Read all data from *sock* until EOF."""
    chunks = []
    while chunk := sock.recv(1024):
        chunks.append(chunk)
    return b"".join(chunks)


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
    if _is_windows_pipe_path(sock_path):
        raw = _send_pipe_request(sock_path, payload_bytes, timeout, retry)
    else:
        with _connect_with_retries(sock_path, timeout, retry) as client:
            client.sendall(payload_bytes)
            client.shutdown(socket.SHUT_WR)
            raw = _read_all(client)

    parsed = parse_json_safely(raw)
    if parsed is None:
        msg = "Invalid JSON from IPC server"
        raise RuntimeError(msg)
    return Response.from_payload(parsed)


def _send_pipe_request(
    sock_path: Path,
    payload_bytes: bytes,
    timeout: float,
    retry: RetryConfig,
) -> bytes:
    """Send *payload_bytes* over a Windows named pipe."""
    if not _HAS_WINDOWS_PIPES:  # pragma: no cover - exercised on Windows
        msg = "pywin32 is required for Windows named pipe IPC"
        raise RuntimeError(msg)

    if win32file is None:  # pragma: no cover - Windows only
        msg = "Named pipe support is unavailable on this platform"
        raise RuntimeError(msg)
    handle = _connect_pipe_with_retries(str(sock_path), timeout, retry)
    try:
        win32file.WriteFile(handle, payload_bytes)
        win32file.FlushFileBuffers(handle)
        return _read_from_pipe(handle)
    finally:
        win32file.CloseHandle(handle)


def invoke_server(
    invocation: Invocation,
    timeout: float,
    retry_config: RetryConfig | None = None,
) -> Response:
    """Send *invocation* to the IPC server and return its response."""
    return _send_request(KIND_INVOCATION, invocation.to_dict(), timeout, retry_config)


def report_passthrough_result(
    result: PassthroughResult,
    timeout: float,
    retry_config: RetryConfig | None = None,
) -> Response:
    """Send passthrough execution results back to the IPC server."""
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
