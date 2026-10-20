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

import socketserver
import typing as typ
from functools import cache

from cmd_mox import _path_utils as path_utils

type _SelectionMode = typ.Literal[
    "native", "compatibility", "windows_unsupported", "unsupported_platform"
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

        _cmd_mox_selection_mode = "windows_unsupported"

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


def _selection_mode(base_server: type[socketserver.BaseServer]) -> _SelectionMode:
    """Describe the selected base without consulting another platform flag.

    Returns
    -------
    _SelectionMode
        The bounded mode that produced *base_server*.
    """
    mode = getattr(base_server, "_cmd_mox_selection_mode", None)
    if mode is not None:
        return typ.cast("_SelectionMode", mode)
    threading_server = getattr(socketserver, "ThreadingUnixStreamServer", None)
    return "native" if base_server is threading_server else "compatibility"


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
