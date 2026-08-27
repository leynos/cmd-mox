"""Utilities for managing IPC Unix domain sockets."""

from __future__ import annotations

import contextlib
import logging
import pathlib
import socket
import time

logger = logging.getLogger(__name__)


def cleanup_stale_socket(socket_path: pathlib.Path) -> None:
    """Remove a pre-existing socket when no server is listening.

    Raises
    ------
    RuntimeError
        If an active server is still listening on *socket_path*.
    """
    socket_path = pathlib.Path(socket_path)
    address = str(socket_path)
    with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as probe:
        try:
            probe.connect(address)
        except OSError:
            pass
        else:
            msg = f"Socket {socket_path} is still in use"
            raise RuntimeError(msg)

    if socket_path.exists():
        try:
            socket_path.unlink()
        except OSError as exc:  # pragma: no cover - unlikely race
            logger.warning("Could not unlink stale socket %s: %s", socket_path, exc)


def _try_socket_connection(address: str, timeout: float) -> bool:
    """Attempt to connect to *address* within *timeout* seconds.

    Returns
    -------
    bool
        ``True`` when the connection succeeded, ``False`` otherwise.
    """
    with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as probe:
        probe.settimeout(timeout)
        try:
            probe.connect(address)
        except OSError:
            return False
    return True


def _poll_socket_until_ready(socket_path: pathlib.Path, timeout: float) -> None:
    """Poll a Unix domain socket until it accepts connections within timeout.

    Raises
    ------
    RuntimeError
        If the socket does not accept connections before *timeout* elapses.
    """
    deadline = time.monotonic() + timeout
    wait_time = 0.001
    address = str(socket_path)

    while True:
        if (remaining := deadline - time.monotonic()) <= 0:
            break

        if _try_socket_connection(address, min(wait_time, remaining)):
            return

        if (remaining := deadline - time.monotonic()) <= 0:
            break

        time.sleep(min(wait_time, remaining))
        wait_time = min(wait_time * 1.5, 0.1)

    msg = f"Socket {socket_path} not accepting connections within timeout"
    raise RuntimeError(msg)


def wait_for_socket(socket_path: pathlib.Path, timeout: float) -> None:
    """Poll for *socket_path* readiness within *timeout* seconds."""
    _poll_socket_until_ready(pathlib.Path(socket_path), timeout)


__all__ = ["cleanup_stale_socket", "wait_for_socket"]
