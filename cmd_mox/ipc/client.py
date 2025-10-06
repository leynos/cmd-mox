"""Client helpers for talking to the IPC server."""

from __future__ import annotations

import dataclasses as dc
import json
import logging
import math
import os
import random
import socket
import time
import typing as t
from pathlib import Path

from cmd_mox._validators import validate_positive_finite_timeout
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV

from .json_utils import parse_json_safely
from .models import Invocation, PassthroughResult, Response

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_RETRIES: t.Final[int] = 3
DEFAULT_CONNECT_BACKOFF: t.Final[float] = 0.05
DEFAULT_CONNECT_JITTER: t.Final[float] = 0.2
MIN_RETRY_SLEEP: t.Final[float] = 0.001


@dc.dataclass(slots=True)
class RetryConfig:
    """Configuration for connection retry behavior."""

    retries: int = DEFAULT_CONNECT_RETRIES
    backoff: float = DEFAULT_CONNECT_BACKOFF
    jitter: float = DEFAULT_CONNECT_JITTER

    def __post_init__(self) -> None:
        """Validate retry configuration values."""
        _validate_retries(self.retries)
        _validate_backoff(self.backoff)
        _validate_jitter(self.jitter)


def _validate_retries(retries: int) -> None:
    """Validate retry attempt count."""
    if retries < 1:
        msg = "retries must be >= 1"
        raise ValueError(msg)


def _validate_connection_timeout(timeout: float) -> None:
    """Validate overall timeout value."""
    validate_positive_finite_timeout(timeout)


def _validate_backoff(backoff: float) -> None:
    """Validate linear backoff value."""
    if not (backoff >= 0 and math.isfinite(backoff)):
        msg = "backoff must be >= 0 and finite"
        raise ValueError(msg)


def _validate_jitter(jitter: float) -> None:
    """Validate jitter fraction."""
    if not (0.0 <= jitter <= 1.0 and math.isfinite(jitter)):
        msg = "jitter must be between 0 and 1 and finite"
        raise ValueError(msg)


def _validate_connection_params(timeout: float, retry_config: RetryConfig) -> None:
    """Ensure connection retry parameters are sensible."""
    _validate_retries(retry_config.retries)
    _validate_connection_timeout(timeout)
    _validate_backoff(retry_config.backoff)
    _validate_jitter(retry_config.jitter)


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


def _create_unix_socket(timeout: float) -> socket.socket:
    """Create a Unix stream socket with *timeout* applied."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    return sock


def _attempt_connection(sock: socket.socket, address: str) -> None:
    """Connect *sock* to *address* or raise on failure."""
    sock.connect(address)


def _connect_with_retries(
    sock_path: Path,
    timeout: float,
    retry_config: RetryConfig,
) -> socket.socket:
    """Connect to *sock_path* retrying on :class:`OSError`."""
    _validate_connection_params(timeout, retry_config)
    address = str(sock_path)
    for attempt in range(retry_config.retries):
        sock = _create_unix_socket(timeout)
        try:
            _attempt_connection(sock, address)
        except OSError as exc:
            sock.close()
            logger.debug(
                "IPC connect attempt %d/%d to %s failed: %s",
                attempt + 1,
                retry_config.retries,
                address,
                exc,
            )
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

    with _connect_with_retries(sock_path, timeout, retry) as client:
        client.sendall(payload_bytes)
        client.shutdown(socket.SHUT_WR)
        raw = _read_all(client)

    parsed = parse_json_safely(raw)
    if parsed is None:
        msg = "Invalid JSON from IPC server"
        raise RuntimeError(msg)
    return Response.from_payload(parsed)


def invoke_server(
    invocation: Invocation,
    timeout: float,
    retry_config: RetryConfig | None = None,
) -> Response:
    """Send *invocation* to the IPC server and return its response."""
    return _send_request("invocation", invocation.to_dict(), timeout, retry_config)


def report_passthrough_result(
    result: PassthroughResult,
    timeout: float,
    retry_config: RetryConfig | None = None,
) -> Response:
    """Send passthrough execution results back to the IPC server."""
    return _send_request("passthrough-result", result.to_dict(), timeout, retry_config)


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
