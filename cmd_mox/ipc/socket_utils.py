"""Utilities for managing IPC Unix domain sockets."""

from __future__ import annotations

import contextlib
import logging
import os
import pathlib
import socket
import time

IS_WINDOWS = os.name == "nt"

logger = logging.getLogger(__name__)


def cleanup_stale_socket(socket_path: pathlib.Path) -> None:
    """Remove a pre-existing socket when no server is listening."""
    socket_path = pathlib.Path(socket_path)
    address = str(socket_path)
    with (
        contextlib.closing(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)) as probe,
        contextlib.suppress(ConnectionRefusedError, OSError),
    ):
        probe.connect(address)
        msg = f"Socket {socket_path} is still in use"
        raise RuntimeError(msg)

    if socket_path.exists():
        try:
            socket_path.unlink()
        except OSError as exc:  # pragma: no cover - unlikely race
            logger.warning("Could not unlink stale socket %s: %s", socket_path, exc)


def wait_for_socket(socket_path: pathlib.Path, timeout: float) -> None:
    """Poll for *socket_path* readiness within *timeout* seconds."""
    socket_path = pathlib.Path(socket_path)
    deadline = time.monotonic() + timeout
    wait_time = 0.001

    if not IS_WINDOWS:
        while time.monotonic() < deadline:
            if socket_path.exists():
                return
            time.sleep(wait_time)
            wait_time = min(wait_time * 1.5, 0.1)
        msg = f"Socket file {socket_path} not created within timeout"
        raise RuntimeError(msg)

    address = str(socket_path)
    while time.monotonic() < deadline:
        with contextlib.closing(
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        ) as probe:
            try:
                probe.settimeout(wait_time)
                probe.connect(address)
            except OSError:
                time.sleep(wait_time)
                wait_time = min(wait_time * 1.5, 0.1)
                continue
        return

    msg = f"Socket {socket_path} not accepting connections within timeout"
    raise RuntimeError(msg)


__all__ = ["cleanup_stale_socket", "wait_for_socket"]
