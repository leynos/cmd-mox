"""Unix domain socket IPC server for CmdMox shims."""

from __future__ import annotations

import contextlib
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
from ._unix_server_base import _selection_mode, resolve_unix_server_base
from .socket_utils import cleanup_stale_socket, wait_for_socket

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from ._unix_server_base import _SelectionMode
    from .models import Invocation, PassthroughResult, Response


BACKEND_SELECTION_OPERATION: typ.Final[str] = "ipc.backend_selection"


class _CacheInfo(typ.Protocol):
    @property
    def hits(self) -> int: ...


type _CacheName = typ.Literal["unix_server_base", "inner_server_class"]
type _CacheOutcome = typ.Literal["hit", "miss"]
type _BackendOutcome = typ.Literal[
    "selected",
    "resolution_failed",
    "cache_access",
    "backend_construction_failed",
    "windows_rejected",
]
type _ErrorCategory = typ.Literal[
    "unsupported_platform",
    "windows_unsupported",
    "os_error",
    "runtime_error",
    "type_error",
    "value_error",
]


class _CacheObservation(typ.NamedTuple):
    name: _CacheName
    outcome: _CacheOutcome


class _InnerServerClass(typ.Protocol):
    """Callable generated server class with its bounded platform selection."""

    _cmd_mox_selection_mode: _SelectionMode

    def __call__(self, socket_path: Path, outer: IPCServer) -> socketserver.BaseServer:
        """Create one transport backend for *outer*."""
        ...


_UNIX_SERVER_BASE_CACHE_LOCK: typ.Final[threading.Lock] = threading.Lock()
_INNER_SERVER_CLASS_CACHE_LOCK: typ.Final[threading.Lock] = threading.Lock()


def _emit_backend_event(
    selection_mode: _SelectionMode,
    outcome: _BackendOutcome,
    cache: _CacheObservation,
    error_category: _ErrorCategory | None = None,
) -> None:
    """Emit a backend event whose dimensions come from fixed local choices."""
    _observability.emit(
        _observability.IPCEvent(
            operation=BACKEND_SELECTION_OPERATION,
            transport="unix",
            selection_mode=selection_mode,
            outcome=outcome,
            cache_name=cache.name,
            cache_outcome=cache.outcome,
            error_category=error_category,
        )
    )


def _cache_outcome(before: _CacheInfo, after: _CacheInfo) -> _CacheOutcome:
    """Classify a cache access from its bounded hit counter.

    Returns
    -------
    _CacheOutcome
        Whether the access found a previously cached value.
    """
    return "hit" if after.hits > before.hits else "miss"


def _resolve_unix_server_base_for_inner_server() -> tuple[
    type[socketserver.BaseServer], _SelectionMode
]:
    """Resolve once for the factory and record bounded decision telemetry.

    Returns
    -------
    tuple[type[socketserver.BaseServer], _SelectionMode]
        The selected base and its bounded platform mode.

    Raises
    ------
    RuntimeError
        If the platform does not provide a Unix server base.
    """
    try:
        with _UNIX_SERVER_BASE_CACHE_LOCK:
            before = resolve_unix_server_base.cache_info()
            base_server = resolve_unix_server_base()
            after = resolve_unix_server_base.cache_info()
    except RuntimeError:
        _emit_backend_event(
            "unsupported_platform",
            "resolution_failed",
            _CacheObservation("unix_server_base", "miss"),
            "unsupported_platform",
        )
        raise

    selection_mode = _selection_mode(base_server)
    if selection_mode in {"native", "compatibility"}:
        _emit_backend_event(
            selection_mode,
            "selected",
            _CacheObservation(
                "unix_server_base",
                _cache_outcome(before, after),
            ),
        )
    return base_server, selection_mode


def _inner_server_cls_for_backend() -> tuple[_InnerServerClass, _CacheOutcome]:
    """Return the cached server class and record its bounded cache outcome.

    Returns
    -------
    tuple[_InnerServerClass, _CacheOutcome]
        The cached class and whether this access hit or missed.

    Raises
    ------
    RuntimeError
        If the platform does not provide a Unix server base.
    """
    try:
        with _INNER_SERVER_CLASS_CACHE_LOCK:
            before = _inner_server_cls.cache_info()
            inner_server_cls = _inner_server_cls()
            after = _inner_server_cls.cache_info()
    except RuntimeError:
        _emit_backend_event(
            "unsupported_platform",
            "cache_access",
            _CacheObservation("inner_server_class", "miss"),
        )
        raise

    cache_outcome = _cache_outcome(before, after)
    inner_server_cls = typ.cast("_InnerServerClass", inner_server_cls)
    selection_mode = inner_server_cls._cmd_mox_selection_mode
    _emit_backend_event(
        selection_mode,
        "cache_access",
        _CacheObservation("inner_server_class", cache_outcome),
    )
    return inner_server_cls, cache_outcome


def _backend_error_category(
    error: Exception, selection_mode: _SelectionMode
) -> _ErrorCategory:
    """Map a construction failure to a bounded category, never its message.

    Returns
    -------
    _ErrorCategory
        A fixed error category suitable for structured logs and counters.

    Raises
    ------
    AssertionError
        If a new exception type reaches this deliberately closed mapping.
    """
    match selection_mode, error:
        case "windows_unsupported", _:
            return "windows_unsupported"
        case _, OSError():
            return "os_error"
        case _, RuntimeError():
            return "runtime_error"
        case _, TypeError():
            return "type_error"
        case _, ValueError():
            return "value_error"
        case _:
            msg = "Unexpected backend construction error type"
            raise AssertionError(msg) from error


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

        Raises
        ------
        OSError
            If the socket backend cannot bind or initialize.
        RuntimeError
            If Unix sockets are unsupported on this platform.
        TypeError
            If the selected backend receives invalid constructor arguments.
        ValueError
            If the backend rejects its constructor configuration.
        """
        inner_server_cls, cache_outcome = _inner_server_cls_for_backend()
        selection_mode = inner_server_cls._cmd_mox_selection_mode
        try:
            server = inner_server_cls(self.socket_path, self)
            server.timeout = self.accept_timeout
            thread = threading.Thread(target=server.serve_forever, daemon=True)
        except (OSError, RuntimeError, TypeError, ValueError) as error:
            outcome = (
                "windows_rejected"
                if selection_mode == "windows_unsupported"
                else "backend_construction_failed"
            )
            _emit_backend_event(
                selection_mode,
                outcome,
                _CacheObservation("inner_server_class", cache_outcome),
                _backend_error_category(error, selection_mode),
            )
            raise
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
    base_server, selection_mode = _resolve_unix_server_base_for_inner_server()
    # Any is needed because the class base is selected at runtime; the resolver
    # guarantees that the value subclasses ``socketserver.BaseServer``.
    base_server = typ.cast("typ.Any", base_server)

    class _InnerServer(base_server):
        """Threaded Unix stream server passing requests to :class:`IPCServer`."""

        _cmd_mox_selection_mode = selection_mode

        def __init__(self, socket_path: Path, outer: IPCServer) -> None:
            """Bind the server to its path and public IPC handler owner."""
            self.outer = outer
            super().__init__(str(socket_path), _IPCHandler)
            self.daemon_threads = True

    return typ.cast(
        "cabc.Callable[[Path, IPCServer], socketserver.BaseServer]",
        _InnerServer,
    )


__all__ = [
    "CallbackIPCServer",
    "IPCHandlers",
    "IPCServer",
    "TimeoutConfig",
]
