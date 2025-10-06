"""Utilities for managing IPC Unix domain sockets."""

from __future__ import annotations

import contextlib
import logging
import pathlib
import socket
import time

logger = logging.getLogger(__name__)


def cleanup_stale_socket(socket_path: pathlib.Path) -> None:
    """Remove a pre-existing socket file if no server is listening."""
    socket_path = pathlib.Path(socket_path)
    if not socket_path.exists():
        return
    try:
        suppress_errors = contextlib.suppress(ConnectionRefusedError, OSError)
        with (
            contextlib.closing(
                socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            ) as probe,
            suppress_errors,
        ):
            probe.connect(str(socket_path))
            msg = f"Socket {socket_path} is still in use"
            raise RuntimeError(msg)
        socket_path.unlink()
    except OSError as exc:  # pragma: no cover - unlikely race
        logger.warning("Could not unlink stale socket %s: %s", socket_path, exc)


def wait_for_socket(socket_path: pathlib.Path, timeout: float) -> None:
    """Poll for *socket_path* to appear within *timeout* seconds."""
    socket_path = pathlib.Path(socket_path)
    timeout_end = time.monotonic() + timeout
    wait_time = 0.001
    while time.monotonic() < timeout_end:
        if socket_path.exists():
            return
        time.sleep(wait_time)
        wait_time = min(wait_time * 1.5, 0.1)
    msg = f"Socket file {socket_path} not created within timeout"
    raise RuntimeError(msg)


__all__ = ["cleanup_stale_socket", "wait_for_socket"]
