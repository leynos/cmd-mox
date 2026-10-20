"""Own lazy resolution of the platform's Unix stream server base class.

This separate module keeps platform fallback logic out of the IPC transport
module. It deliberately imports no transport module, avoiding an import cycle.

Resolve the base class only when a transport server is needed:

>>> from cmd_mox.ipc._unix_server_base import resolve_unix_server_base
>>> import socketserver
>>> base_server = resolve_unix_server_base()
>>> issubclass(base_server, socketserver.BaseServer)
True
"""

import logging
import socketserver
import typing as typ
from functools import cache

from cmd_mox import _path_utils as path_utils

from . import _observability

_LOGGER = logging.getLogger(__name__)
_RESOLUTION_OPERATION = "ipc.unix_server_base.resolve"
type _ResolutionOutcome = typ.Literal[
    "native",
    "compatibility",
    "windows_unsupported",
    "unsupported",
]


def _create_unsupported_unix_server() -> type[socketserver.BaseServer]:
    """Build a placeholder for unsupported Windows Unix servers.

    Returns
    -------
    type[socketserver.BaseServer]
        A server class that always raises on construction.
    """

    class _UnsupportedUnixServer(socketserver.BaseServer):
        """Placeholder that raises when Unix sockets are requested on Windows."""

        def __init__(self, *args: object, **kwargs: object) -> None:
            """Reject construction on Windows.

            Raises
            ------
            RuntimeError
                Unix stream servers are unavailable on Windows.
            """
            msg = "Unix domain socket servers are unavailable on Windows"
            raise RuntimeError(msg)

    return _UnsupportedUnixServer


def _emit_resolution_event(
    outcome: _ResolutionOutcome,
    *,
    error_category: typ.Literal["RuntimeError"] | None = None,
) -> None:
    """Record a bounded platform-selection outcome."""
    _observability.emit(
        _observability.IPCEvent(
            operation=_RESOLUTION_OPERATION,
            transport="unix",
            outcome=outcome,
            error_category=error_category,
        ),
        logger=_LOGGER,
    )


def _resolve_unix_server_base() -> type[socketserver.BaseServer]:
    """Choose the platform's threaded Unix server class.

    Returns
    -------
    type[socketserver.BaseServer]
        The native or compatibility server class.

    Raises
    ------
    RuntimeError
        If the platform lacks Unix domain socket server classes.
    """
    if path_utils.IS_WINDOWS:
        _emit_resolution_event("windows_unsupported", error_category="RuntimeError")
        return _create_unsupported_unix_server()
    threading_server = getattr(socketserver, "ThreadingUnixStreamServer", None)
    if threading_server is not None:
        _emit_resolution_event("native")
        return threading_server
    unix_server = getattr(socketserver, "UnixStreamServer", None)
    if unix_server is not None:

        class _ThreadingUnixCompat(
            socketserver.ThreadingMixIn,
            unix_server,
        ):
            """Threading shim for platforms lacking ThreadingUnixStreamServer."""

            pass

        _emit_resolution_event("compatibility")
        return _ThreadingUnixCompat
    msg = "Unix domain socket servers are not supported on this platform"
    _emit_resolution_event("unsupported", error_category="RuntimeError")
    raise RuntimeError(msg)


@cache
def resolve_unix_server_base() -> type[socketserver.BaseServer]:
    """Return the platform's Unix stream server base class, resolving it once.

    Returns
    -------
    type[socketserver.BaseServer]
        The threaded Unix stream server class available on this platform.

    Notes
    -----
    On Windows, the returned placeholder raises ``RuntimeError`` if
    instantiated. On other platforms, resolution raises ``RuntimeError`` if
    no Unix domain socket server class is available.
    """
    return _resolve_unix_server_base()
