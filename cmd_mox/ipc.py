"""JSON IPC server and client helpers for CmdMox."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import json
import logging
import math
import os
import socket
import socketserver
import threading
import time
import typing as t
from pathlib import Path

from .environment import CMOX_IPC_SOCKET_ENV

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types

logger = logging.getLogger(__name__)

DEFAULT_CONNECT_RETRIES: t.Final[int] = 3
DEFAULT_CONNECT_BACKOFF: t.Final[float] = 0.05


@dc.dataclass(slots=True)
class Invocation:
    """Information reported by a shim to the IPC server."""

    command: str
    args: list[str]
    stdin: str
    env: dict[str, str]


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


def _validate_connection_params(retries: int, timeout: float, backoff: float) -> None:
    """Ensure connection retry parameters are sensible."""
    if retries < 1:
        msg = "retries must be >= 1"
        raise ValueError(msg)
    if not (timeout > 0 and math.isfinite(timeout)):
        msg = "timeout must be > 0"
        raise ValueError(msg)
    if not (backoff >= 0 and math.isfinite(backoff)):
        msg = "backoff must be >= 0"
        raise ValueError(msg)


def _connect_with_retries(
    sock_path: Path,
    timeout: float,
    retries: int,
    backoff: float,
) -> socket.socket:
    """Connect to *sock_path* retrying on :class:`OSError`.

    Opens an ``AF_UNIX`` socket and performs ``retries`` connection attempts.
    ``retries`` is the total number of attempts and must be at least one. The
    delay between attempts scales linearly with ``backoff`` and the attempt
    number. ``timeout`` must be positive and ``backoff`` non-negative. The
    last ``OSError`` is raised if every attempt fails.
    """
    _validate_connection_params(retries, timeout, backoff)
    address = str(sock_path)
    for attempt in range(retries):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(address)
        except OSError as exc:
            sock.close()
            logger.debug(
                "IPC connect attempt %d/%d to %s failed: %s",
                attempt + 1,
                retries,
                address,
                exc,
            )
            if attempt < retries - 1:
                delay = backoff * (attempt + 1)
                # Optional: add jitter to spread retries under contention
                # delay *= 0.8 + random.random() * 0.4
                time.sleep(delay)
                continue
            raise
        else:
            return sock
    # Loop always returns or raises; this is defensive.
    raise RuntimeError  # pragma: no cover


class _IPCHandler(socketserver.StreamRequestHandler):
    """Handle a single shim connection."""

    def handle(self) -> None:  # pragma: no cover - exercised via behaviour tests
        raw = self.rfile.read()
        try:
            payload = json.loads(raw.decode())
        except json.JSONDecodeError:
            logger.exception("IPC received malformed JSON")
            return
        if not isinstance(payload, dict):
            logger.error("IPC payload not a dict: %r", payload)
            return
        try:
            payload_dict = t.cast("dict[str, t.Any]", payload)
            invocation = Invocation(**payload_dict)  # type: ignore[arg-type] - payload validated
        except TypeError:
            logger.exception("IPC payload missing required fields: %r", payload)
            return
        response = self.server.outer.handle_invocation(  # type: ignore[attr-defined]
            invocation
        )
        self.wfile.write(json.dumps(dc.asdict(response)).encode())
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

        if self.socket_path.exists():
            try:
                # Verify the socket isn't in use before removing it to avoid
                # clobbering another process's server.
                probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    probe.connect(str(self.socket_path))
                    probe.close()
                    msg = f"Socket {self.socket_path} is still in use"
                    raise RuntimeError(msg)
                except (ConnectionRefusedError, OSError):
                    # No listener found; safe to remove the stale file.
                    pass
                self.socket_path.unlink()
            except OSError as exc:  # pragma: no cover - unlikely race
                logger.warning(
                    "Could not unlink stale socket %s: %s", self.socket_path, exc
                )

        self._server = _InnerServer(self.socket_path, self)
        self._server.timeout = self.accept_timeout
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        # Poll for the socket path using exponential backoff to avoid races on
        # slower systems while failing quickly when something goes wrong.
        timeout_end = time.time() + self.timeout
        wait_time = 0.001
        while time.time() < timeout_end:
            if self.socket_path.exists():
                break
            time.sleep(wait_time)
            wait_time = min(wait_time * 1.5, 0.1)

        if not self.socket_path.exists():
            msg = f"Socket file {self.socket_path} not created within timeout"
            raise RuntimeError(msg)

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
    retries: int = DEFAULT_CONNECT_RETRIES,
    backoff: float = DEFAULT_CONNECT_BACKOFF,
) -> Response:
    """Send *invocation* to the IPC server and return its response.

    Attempts to connect ``retries`` times (default:
    :data:`DEFAULT_CONNECT_RETRIES`) with a linear backoff of ``backoff``
    seconds (default: :data:`DEFAULT_CONNECT_BACKOFF`). ``retries`` counts the
    total number of attempts and must be at least one. ``timeout`` must be
    positive and ``backoff`` non-negative. The underlying :class:`OSError`
    bubbles up if the client cannot connect. The same timeout also applies
    to send/receive operations and may surface as :class:`socket.timeout`.
    A :class:`ValueError` is raised for invalid parameters and
    :class:`RuntimeError` if the server responds with invalid JSON.
    """
    sock = os.environ.get(CMOX_IPC_SOCKET_ENV)
    if sock is None:
        msg = f"{CMOX_IPC_SOCKET_ENV} is not set"
        raise RuntimeError(msg)

    sock_path = Path(sock)
    with _connect_with_retries(sock_path, timeout, retries, backoff) as client:
        payload_bytes = json.dumps(dc.asdict(invocation)).encode("utf-8")
        client.sendall(payload_bytes)
        client.shutdown(socket.SHUT_WR)
        raw = _read_all(client)

    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        msg = "Invalid JSON from IPC server"
        raise RuntimeError(msg) from exc
    if not isinstance(payload, dict):
        msg = f"Invalid JSON object from IPC server: {type(payload).__name__}"
        raise RuntimeError(msg)  # noqa: TRY004
    return Response(**t.cast("dict[str, t.Any]", payload))
