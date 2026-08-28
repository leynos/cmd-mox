"""Unix domain socket IPC server for CmdMox shims."""

from __future__ import annotations

import contextlib
import socketserver
import threading
import typing as typ

from cmd_mox import _path_utils as path_utils

from ._server_core import (
    IPCHandlers,
    TimeoutConfig,
    _BaseIPCServer,
    _request_pipeline,
)
from .socket_utils import cleanup_stale_socket, wait_for_socket


def _create_unsupported_unix_server() -> type[socketserver.BaseServer]:
    class _UnsupportedUnixServer(socketserver.BaseServer):
        """Placeholder that raises when Unix sockets are requested on Windows."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            msg = "Unix domain socket servers are unavailable on Windows"
            raise RuntimeError(msg)

    return _UnsupportedUnixServer


def _resolve_unix_server_base() -> type[socketserver.BaseServer]:
    if path_utils.IS_WINDOWS:
        return _create_unsupported_unix_server()
    threading_server = getattr(socketserver, "ThreadingUnixStreamServer", None)
    if threading_server is not None:
        return threading_server
    unix_server = getattr(socketserver, "UnixStreamServer", None)
    if unix_server is not None:

        class _ThreadingUnixCompat(
            socketserver.ThreadingMixIn,
            unix_server,
        ):
            """Threading shim for platforms lacking ThreadingUnixStreamServer."""

            pass

        return _ThreadingUnixCompat
    msg = "Unix domain socket servers are not supported on this platform"
    raise RuntimeError(msg)


if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path
    from socketserver import ThreadingUnixStreamServer as _BaseUnixServer

    from .models import Invocation, PassthroughResult, Response
else:
    _BaseUnixServer = _resolve_unix_server_base()


class IPCServer(_BaseIPCServer["_InnerServer"]):
    """Run a Unix domain socket server for shims."""

    def _prepare_backend_start(self) -> None:
        cleanup_stale_socket(self.socket_path)

    def _create_backend(self) -> tuple[_InnerServer, threading.Thread]:
        server = _InnerServer(self.socket_path, self)
        server.timeout = self.accept_timeout
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        return server, thread

    def _wait_until_ready(self) -> None:
        wait_for_socket(self.socket_path, self.timeout)

    @staticmethod
    def _stop_backend(server: _InnerServer | None) -> None:
        if server is None:
            return
        server.shutdown()
        server.server_close()

    def _post_stop_cleanup(self) -> None:
        if self.socket_path.exists():
            with contextlib.suppress(OSError):
                self.socket_path.unlink()


class CallbackIPCServer(IPCServer):
    """IPCServer variant that delegates to callbacks."""

    def __init__(
        self,
        socket_path: Path,
        handler: cabc.Callable[[Invocation], Response],
        passthrough_handler: cabc.Callable[[PassthroughResult], Response],
        *,
        timeouts: TimeoutConfig | None = None,
    ) -> None:
        """Initialise a callback-driven IPC server."""
        timeouts = timeouts or TimeoutConfig()
        super().__init__(
            socket_path,
            timeout=timeouts.timeout,
            accept_timeout=timeouts.accept_timeout,
            handlers=IPCHandlers(
                handler=handler,
                passthrough_handler=passthrough_handler,
            ),
        )


class _IPCHandler(socketserver.StreamRequestHandler):
    """Handle a single shim connection."""

    def handle(self) -> None:  # pragma: no cover - exercised via behaviour tests
        raw = self.rfile.read()
        response_bytes = _request_pipeline(self.server.outer, raw, "unix")  # type: ignore[attr-defined, ty:unresolved-attribute]
        if response_bytes is None:
            return
        self.wfile.write(response_bytes)
        self.wfile.flush()


class _InnerServer(_BaseUnixServer):
    """Threaded Unix stream server passing requests to :class:`IPCServer`."""

    def __init__(self, socket_path: Path, outer: IPCServer) -> None:
        self.outer = outer
        super().__init__(str(socket_path), _IPCHandler)
        self.daemon_threads = True


__all__ = [
    "CallbackIPCServer",
    "IPCHandlers",
    "IPCServer",
    "TimeoutConfig",
]
