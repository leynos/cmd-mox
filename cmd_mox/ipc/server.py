"""Unix domain socket IPC server for CmdMox shims."""

from __future__ import annotations

import contextlib
import logging
import socketserver
import threading
import typing as typ
from functools import cache

from . import _observability
from ._server_core import (
    IPCHandlers,
    TimeoutConfig,
    _BaseIPCServer,
    _request_pipeline,
)
from ._unix_server_base import resolve_unix_server_base
from .socket_utils import cleanup_stale_socket, wait_for_socket

_LOGGER = logging.getLogger(__name__)
_INNER_SERVER_FACTORY_LOCK = threading.Lock()

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from .models import Invocation, PassthroughResult, Response


class IPCServer(_BaseIPCServer[socketserver.BaseServer]):
    """Run a Unix domain socket server for shims."""

    def _prepare_backend_start(self) -> None:
        cleanup_stale_socket(self.socket_path)

    def _create_backend(self) -> tuple[socketserver.BaseServer, threading.Thread]:
        """Build the cached Unix socket backend and its daemon thread.

        Returns
        -------
        tuple[socketserver.BaseServer, threading.Thread]
            The server instance and its daemon serving thread.
        """
        inner_server_cls = _inner_server_cls_for_backend()
        server = inner_server_cls(self.socket_path, self)
        server.timeout = self.accept_timeout
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        return server, thread

    def _wait_until_ready(self) -> None:
        wait_for_socket(self.socket_path, self.timeout)

    @staticmethod
    def _stop_backend(server: socketserver.BaseServer | None) -> None:
        """Shut down and close a backend when one was created."""
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


@cache
def _inner_server_cls() -> cabc.Callable[[Path, IPCServer], socketserver.BaseServer]:
    """Build the transport server class after resolving its platform base.

    Returns
    -------
    cabc.Callable[[Path, IPCServer], socketserver.BaseServer]
        The inner server class configured for this platform.
    """
    base_server = typ.cast("typ.Any", resolve_unix_server_base())

    class _InnerServer(base_server):
        """Threaded Unix stream server passing requests to :class:`IPCServer`."""

        def __init__(self, socket_path: Path, outer: IPCServer) -> None:
            """Bind the server to its path and public IPC handler owner."""
            self.outer = outer
            super().__init__(str(socket_path), _IPCHandler)
            self.daemon_threads = True

    return typ.cast(
        "cabc.Callable[[Path, IPCServer], socketserver.BaseServer]",
        _InnerServer,
    )


def _emit_cache_event(
    operation: typ.Literal[
        "ipc.unix_server_base.cache",
        "ipc.unix_server_factory.cache",
    ],
    *,
    cache_missed: bool,
) -> None:
    """Record one bounded lazy-cache outcome."""
    outcome = "miss" if cache_missed else "hit"
    _observability.emit(
        _observability.IPCEvent(
            operation=operation,
            transport="unix",
            outcome=outcome,
        ),
        logger=_LOGGER,
    )


def _inner_server_cls_for_backend() -> cabc.Callable[
    [Path, IPCServer], socketserver.BaseServer
]:
    """Return the cached factory class and count bounded cache outcomes.

    Returns
    -------
    cabc.Callable[[Path, IPCServer], socketserver.BaseServer]
        The inner server class configured for this platform.
    """
    with _INNER_SERVER_FACTORY_LOCK:
        resolver_before = resolve_unix_server_base.cache_info()
        factory_before = _inner_server_cls.cache_info()
        inner_server_cls = _inner_server_cls()
        resolver_after = resolve_unix_server_base.cache_info()
        factory_after = _inner_server_cls.cache_info()
        _emit_cache_event(
            "ipc.unix_server_base.cache",
            cache_missed=resolver_after.misses > resolver_before.misses,
        )
        _emit_cache_event(
            "ipc.unix_server_factory.cache",
            cache_missed=factory_after.misses > factory_before.misses,
        )
    return inner_server_cls


__all__ = [
    "CallbackIPCServer",
    "IPCHandlers",
    "IPCServer",
    "TimeoutConfig",
]
