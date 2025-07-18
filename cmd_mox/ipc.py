"""JSON IPC server and client helpers for CmdMox."""

from __future__ import annotations

import contextlib
import dataclasses as dc
import json
import logging
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


def _read_all(sock: socket.socket) -> bytes:
    """Read all data from *sock* until EOF."""
    chunks = []
    while chunk := sock.recv(1024):
        chunks.append(chunk)
    return b"".join(chunks)


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
    """Run a Unix domain socket server for shims."""

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
        self._stop = threading.Event()

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

        self._stop.clear()
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
        self._stop.set()
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


def invoke_server(invocation: Invocation, timeout: float) -> Response:
    """Send *invocation* to the IPC server and return its response."""
    sock = os.environ.get(CMOX_IPC_SOCKET_ENV)
    if sock is None:
        msg = f"{CMOX_IPC_SOCKET_ENV} is not set"
        raise RuntimeError(msg)

    sock_path = Path(sock)
    attempt = 0
    while True:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(timeout)
                client.connect(str(sock_path))
                client.sendall(json.dumps(dc.asdict(invocation)).encode())
                client.shutdown(socket.SHUT_WR)
                raw = _read_all(client)
            break
        except OSError:
            if attempt >= 2:
                raise
            time.sleep(0.05 * (attempt + 1))
            attempt += 1

    try:
        payload = json.loads(raw.decode())
    except json.JSONDecodeError as exc:
        msg = "Invalid JSON from IPC server"
        raise RuntimeError(msg) from exc

    payload_dict = t.cast("dict[str, t.Any]", payload)
    return Response(**payload_dict)
