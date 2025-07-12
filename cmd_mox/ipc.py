"""Simple IPC server and client helpers for CmdMox."""

from __future__ import annotations

import dataclasses as dc
import json
import os
import socket
import threading
import typing as t
from pathlib import Path

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types


@dc.dataclass
class Invocation:
    """Information sent from a shim to the IPC server."""

    command: str
    args: list[str]
    stdin: str
    env: dict[str, str]


@dc.dataclass
class Response:
    """Response from the IPC server to a shim."""

    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0


class IPCServer:
    """Lightweight JSON IPC server using a Unix domain socket."""

    def __init__(self, socket_path: Path, timeout: float = 5.0) -> None:
        self.socket_path = Path(socket_path)
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

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

    def start(self) -> None:
        """Start the server in a background thread."""
        if self._thread:
            msg = "IPC server already started"
            raise RuntimeError(msg)
        if self.socket_path.exists():
            self.socket_path.unlink()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.socket_path))
        self._sock.listen()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the server and clean up the socket."""
        self._stop.set()
        if self._sock:
            try:
                self._sock.close()
            finally:
                self._sock = None
        if self._thread:
            self._thread.join(timeout=self.timeout)
            self._thread = None
        if self.socket_path.exists():
            self.socket_path.unlink()

    def _serve(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while not self._stop.is_set():
            try:
                sock.settimeout(0.1)
                conn, _ = sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            with conn:
                conn.settimeout(self.timeout)
                try:
                    data = conn.recv(1024)
                    buffer = data
                    while data:
                        data = conn.recv(1024)
                        buffer += data
                    payload = t.cast("dict[str, t.Any]", json.loads(buffer.decode()))
                    invocation = Invocation(**payload)  # type: ignore[arg-type]
                    response = self.handle_invocation(invocation)
                    conn.sendall(json.dumps(dc.asdict(response)).encode())
                except (OSError, json.JSONDecodeError):
                    # Ignore malformed requests or connection errors
                    continue

    def handle_invocation(self, invocation: Invocation) -> Response:
        """Echo the command name in ``stdout``."""
        return Response(stdout=invocation.command)


def invoke_server(invocation: Invocation, timeout: float) -> Response:
    """Send *invocation* to the IPC server and return its response."""
    sock_path = Path(os.environ["CMOX_IPC_SOCKET"])  # raised KeyError if unset
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout)
        client.connect(str(sock_path))
        client.sendall(json.dumps(dc.asdict(invocation)).encode())
        client.shutdown(socket.SHUT_WR)
        buffer = b""
        while True:
            chunk = client.recv(1024)
            if not chunk:
                break
            buffer += chunk
    payload = json.loads(buffer.decode())
    return Response(**payload)
