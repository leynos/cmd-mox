"""Client helpers for talking to the IPC server."""

from __future__ import annotations

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
