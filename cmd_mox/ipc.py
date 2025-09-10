"""JSON IPC server and client helpers for CmdMox."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import json
import logging
import math
import os
import random
import re
import socket
import socketserver
import threading
import time
import typing as t
from pathlib import Path

from .environment import CMOX_IPC_SOCKET_ENV
from .expectations import SENSITIVE_ENV_KEY_TOKENS

# Pre-normalize tokens once for case-insensitive checks
_SENSITIVE_TOKENS: tuple[str, ...] = tuple(
    tok.casefold() for tok in SENSITIVE_ENV_KEY_TOKENS
)

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types

logger = logging.getLogger(__name__)

SENSITIVE_ENV_KEY_PATTERN: t.Final[re.Pattern[str]] = re.compile(
    r"(?:^|_|\b)(?:key|token|secret|password)(?:_|$|\b)", re.IGNORECASE
)

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
        # Validate only retry-related parameters here to avoid coupling to timeout.
        _validate_retries(self.retries)
        _validate_backoff(self.backoff)
        _validate_jitter(self.jitter)


@dc.dataclass(slots=True)
class Invocation:
    """Information reported by a shim to the IPC server."""

    command: str
    args: list[str]
    stdin: str
    env: dict[str, str]
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0

    def to_dict(self) -> dict[str, t.Any]:
        """Return a JSON-serializable mapping of this invocation."""
        return {
            "command": self.command,
            "args": list(self.args),
            "stdin": self.stdin,
            "env": dict(self.env),
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.exit_code,
        }

    def __repr__(self) -> str:
        """Return a convenient debug representation."""
        data = self.to_dict()

        for key in list(data["env"]):
            k = key.casefold()
            # Merge: use refined token-based check and main's regex.
            if any(tok in k for tok in _SENSITIVE_TOKENS) or SENSITIVE_ENV_KEY_PATTERN.search(key):
                data["env"][key] = "<redacted>"

        def _truncate(s: str, limit: int = 256) -> str:
            if limit <= 1:
                return "" if len(s) <= limit else "…"
            return s if len(s) <= limit else f"{s[: limit - 1]}…"

        for field in ("stdin", "stdout", "stderr"):
            data[field] = _truncate(data[field])

        return f"Invocation({data!r})"


@dc.dataclass(slots=True)
class Response:
    """Response from the IPC server back to a shim."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    env: dict[str, str] = dc.field(default_factory=dict)


def _read_all(sock: socket.socket) -> bytes:
    """Read all data from *sock* until EOF."""
    chunks = []
    while chunk := sock.recv(1024):
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_json_safely(data: bytes) -> dict[str, t.Any] | None:
    """Return a JSON object parsed from *data* or ``None`` on failure."""
    try:
        payload = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return t.cast("dict[str, t.Any]", payload)


def _validate_invocation_payload(payload: dict[str, t.Any]) -> Invocation | None:
    """Return an :class:`Invocation` if *payload* has the required fields."""
    try:
        return Invocation(**payload)  # type: ignore[arg-type]
    except TypeError:
        logger.exception("IPC payload missing required fields: %r", payload)
        return None


def _get_validated_socket_path() -> Path:
    """Fetch the IPC socket path from the environment."""
    sock = os.environ.get(CMOX_IPC_SOCKET_ENV)
    if sock is None:
        msg = f"{CMOX_IPC_SOCKET_ENV} is not set"
        raise RuntimeError(msg)
    return Path(sock)


def _create_unix_socket(timeout: float) -> socket.socket:
    """Create a Unix stream socket with *timeout* applied."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    return sock


def _attempt_connection(sock: socket.socket, address: str) -> bool:
    """Attempt to connect *sock* to *address* returning ``True`` on success."""
    sock.connect(address)
    return True


def _validate_retries(retries: int) -> None:
    """Validate retry attempt count."""
    if retries < 1:
        msg = "retries must be >= 1"
        raise ValueError(msg)


def _validate_timeout(timeout: float) -> None:
    """Validate overall timeout value."""
    if not (timeout > 0 and math.isfinite(timeout)):
        msg = "timeout must be > 0 and finite"
        raise ValueError(msg)


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
    _validate_timeout(timeout)
    _validate_backoff(retry_config.backoff)
    _validate_jitter(retry_config.jitter)


def calculate_retry_delay(attempt: int, backoff: float, jitter: float) -> float:
    """Return the sleep delay for a 0-based *attempt*; never shorter than
    MIN_RETRY_SLEEP.
    """
    delay = backoff * (attempt + 1)
    if jitter:
        # Randomise the linear backoff within the jitter bounds to avoid
        # thundering herds if many clients retry simultaneously.
        factor = random.uniform(1.0 - jitter, 1.0 + jitter)  # noqa: S311
        delay *= factor
    # Prevent a zero-length sleep which can spin on some platforms.
    return max(delay, MIN_RETRY_SLEEP)


def _connect_with_retries(
    sock_path: Path,
    timeout: float,
    retry_config: RetryConfig,
) -> socket.socket:
    """Connect to *sock_path* retrying on :class:`OSError`.

    Opens an ``AF_UNIX`` socket and performs ``retry_config.retries`` connection
    attempts. ``retry_config.retries`` is the total number of attempts and must
    be at least one. The delay between attempts scales linearly with
    ``retry_config.backoff`` and the attempt number. ``timeout`` must be
    positive, ``retry_config.backoff`` non-negative, and ``retry_config.jitter``
    within ``[0, 1]``. The last ``OSError`` is raised if every attempt fails.
    """
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
    # Loop always returns or raises; this is defensive.
    msg = (
        "Unreachable code reached in socket connection loop: all retry "
        "attempts exhausted and no socket returned. This indicates a logic "
        "error or unexpected control flow."
    )
    raise RuntimeError(msg)  # pragma: no cover


def _cleanup_stale_socket(socket_path: Path) -> None:
    """Remove a pre-existing socket file if no server is listening."""
    if not socket_path.exists():
        return
    try:
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(socket_path))
            probe.close()
            msg = f"Socket {socket_path} is still in use"
            raise RuntimeError(msg)
        except (ConnectionRefusedError, OSError):
            pass
        socket_path.unlink()
    except OSError as exc:  # pragma: no cover - unlikely race
        logger.warning("Could not unlink stale socket %s: %s", socket_path, exc)


def _wait_for_socket(socket_path: Path, timeout: float) -> None:
    """Poll for *socket_path* to appear within *timeout* seconds."""
    timeout_end = time.monotonic() + timeout
    wait_time = 0.001
    while time.monotonic() < timeout_end:
        if socket_path.exists():
            return
        time.sleep(wait_time)
        wait_time = min(wait_time * 1.5, 0.1)
    msg = f"Socket file {socket_path} not created within timeout"
    raise RuntimeError(msg)


class _IPCHandler(socketserver.StreamRequestHandler):
    """Handle a single shim connection."""

    def handle(self) -> None:  # pragma: no cover - exercised via behaviour tests
        raw = self.rfile.read()
        payload = _parse_json_safely(raw)
        if payload is None:
            try:
                obj = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                logger.exception("IPC received malformed JSON")
            else:
                logger.error("IPC payload not a dict: %r", obj)
            return
        invocation = _validate_invocation_payload(payload)
        if invocation is None:
            return
        response = self.server.outer.handle_invocation(  # type: ignore[attr-defined]
            invocation
        )
        self.wfile.write(json.dumps(dc.asdict(response)).encode("utf-8"))
        self.wfile.flush()


class _InnerServer(socketserver.ThreadingUnixStreamServer):
    """Threaded Unix stream server passing requests to :class:`IPCServer`."""

    def __init__(self, socket_path: Path, outer: IPCServer) -> None:
        self.outer = outer
        super().__init__(str(socket_path), _IPCHandler)
        self.daemon_threads = True


class IPCServer:
    """Run a Unix domain socket server for shims.

    The server listens on a Unix domain socket created by
    :class:`~cmd_mox.environment.EnvironmentManager`. Clients connect via the
    ``CMOX_IPC_SOCKET`` path and communicate using JSON messages. Connection
    attempts default to a five second timeout, but this can be overridden by
    setting :data:`~cmd_mox.environment.CMOX_IPC_TIMEOUT_ENV` in the
    environment. See the `IPC server` section of the design document for
    details on the rationale and configuration:
    ``docs/python-native-command-mocking-design.md``.
    """

    def __init__(
        self,
        socket_path: Path,
        timeout: float = 5.0,
        accept_timeout: float | None = None,
    ) -> None:
        """Create a server listening at *socket_path*.

        ``timeout`` controls startup and shutdown waits. ``accept_timeout``
        determines how often the server checks for shutdown requests while
        waiting for incoming connections. If not provided, it defaults to one
        tenth of ``timeout`` capped at 0.1 seconds.
        """
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self.accept_timeout = accept_timeout or min(0.1, timeout / 10)
        self._server: _InnerServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------
    def __enter__(self) -> IPCServer:
        """Start the server when entering a context."""
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        """Stop the server when leaving a context."""
        self.stop()

    # ------------------------------------------------------------------
    # Lifecycle management
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Start the background server thread."""
        if self._thread:
            msg = "IPC server already started"
            raise RuntimeError(msg)

        _cleanup_stale_socket(self.socket_path)

        self._server = _InnerServer(self.socket_path, self)
        self._server.timeout = self.accept_timeout
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        _wait_for_socket(self.socket_path, self.timeout)

    def stop(self) -> None:
        """Stop the server and clean up the socket."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join(self.timeout)
            self._thread = None
        self._server = None
        if self.socket_path.exists():
            with contextlib.suppress(OSError):
                self.socket_path.unlink()

    # ------------------------------------------------------------------
    # Request processing
    # ------------------------------------------------------------------
    def handle_invocation(self, invocation: Invocation) -> Response:
        """Echo the command name by default."""
        return Response(stdout=invocation.command)


def invoke_server(
    invocation: Invocation,
    timeout: float,
    retry_config: RetryConfig | None = None,
) -> Response:
    """Send *invocation* to the IPC server and return its response.

    Attempts to connect ``retry_config.retries`` times (default:
    :data:`DEFAULT_CONNECT_RETRIES`) with a linear backoff of
    ``retry_config.backoff`` seconds (default:
    :data:`DEFAULT_CONNECT_BACKOFF`). A jitter fraction (default:
    :data:`DEFAULT_CONNECT_JITTER`) spreads delays to avoid synchronized retries.
    ``retry_config.retries`` counts the total number of attempts and must be at
    least one. ``timeout`` must be positive,
    ``retry_config.backoff`` non-negative, and ``retry_config.jitter`` within
    ``[0, 1]``. The underlying
    :class:`OSError`
    bubbles up if the client cannot connect. The same timeout also applies
    to send/receive operations and may surface as :class:`socket.timeout`.
    A :class:`ValueError` is raised for invalid parameters and
    :class:`RuntimeError` if the server responds with invalid JSON.
    """
    retry_config = retry_config or RetryConfig()
    sock_path = _get_validated_socket_path()
    with _connect_with_retries(sock_path, timeout, retry_config) as client:
        payload_bytes = json.dumps(dc.asdict(invocation)).encode("utf-8")
        client.sendall(payload_bytes)
        client.shutdown(socket.SHUT_WR)
        raw = _read_all(client)

    payload = _parse_json_safely(raw)
    if payload is None:
        msg = "Invalid JSON from IPC server"
        raise RuntimeError(msg)
    return Response(**payload)
