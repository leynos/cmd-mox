"""Utilities for managing IPC Unix domain sockets."""

from __future__ import annotations

import contextlib
import logging
import pathlib
import socket
import time

logger = logging.getLogger(__name__)


def cleanup_stale_socket(socket_path: pathlib.Path) -> None:
    """Remove a pre-existing socket when no server is listening."""
    socket_path = pathlib.Path(socket_path)
    address = str(socket_path)
    with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as probe:
        try:
            probe.connect(address)
        except (ConnectionRefusedError, OSError):
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
    """Attempt to connect to *address* within *timeout* seconds."""
    with contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as probe:
        probe.settimeout(timeout)
        try:
            probe.connect(address)
        except (FileNotFoundError, ConnectionRefusedError, OSError):
            return False
    return True


def _poll_socket_until_ready(socket_path: pathlib.Path, timeout: float) -> None:
    """Poll a Unix domain socket until it accepts connections within timeout."""
    deadline = time.monotonic() + timeout
    wait_time = 0.001
    address = str(socket_path)

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        attempt = min(wait_time, remaining)
        if _try_socket_connection(address, attempt):
            return

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        time.sleep(min(wait_time, remaining))
        wait_time = min(wait_time * 1.5, 0.1)

    msg = f"Socket {socket_path} not accepting connections within timeout"
    raise RuntimeError(msg)


def wait_for_socket(socket_path: pathlib.Path, timeout: float) -> None:
    """Poll for *socket_path* readiness within *timeout* seconds."""
    _poll_socket_until_ready(pathlib.Path(socket_path), timeout)


__all__ = ["cleanup_stale_socket", "wait_for_socket"]
