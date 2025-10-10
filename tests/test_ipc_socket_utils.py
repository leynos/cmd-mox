"""Tests for IPC socket utility helpers."""

from __future__ import annotations

import pathlib
import socket
import threading
import time

import pytest

from cmd_mox.ipc.socket_utils import cleanup_stale_socket, wait_for_socket

pytestmark = pytest.mark.requires_unix_sockets


def test_cleanup_stale_socket_removes_unbound_file(tmp_path: pathlib.Path) -> None:
    """cleanup_stale_socket should unlink orphaned socket files."""
    tmp_path = pathlib.Path(tmp_path)
    socket_path = tmp_path / "ipc.sock"
    socket_path.write_bytes(b"")

    cleanup_stale_socket(socket_path)

    assert not socket_path.exists()


def test_cleanup_stale_socket_refuses_active_socket(tmp_path: pathlib.Path) -> None:
    """cleanup_stale_socket should not remove sockets with active listeners."""
    tmp_path = pathlib.Path(tmp_path)
    socket_path = tmp_path / "ipc.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen()

    try:
        with pytest.raises(RuntimeError, match="still in use"):
            cleanup_stale_socket(socket_path)
        assert socket_path.exists()
    finally:
        server.close()
        if socket_path.exists():
            socket_path.unlink()


def test_wait_for_socket_succeeds_when_file_appears(tmp_path: pathlib.Path) -> None:
    """wait_for_socket should poll until the socket path appears."""
    tmp_path = pathlib.Path(tmp_path)
    socket_path = tmp_path / "ipc.sock"

    def _create_socket() -> None:
        time.sleep(0.05)
        socket_path.write_bytes(b"")

    thread = threading.Thread(target=_create_socket)
    thread.start()
    try:
        wait_for_socket(socket_path, timeout=1.0)
    finally:
        thread.join()


def test_wait_for_socket_times_out(tmp_path: pathlib.Path) -> None:
    """wait_for_socket should raise when the socket path never appears."""
    tmp_path = pathlib.Path(tmp_path)
    with pytest.raises(RuntimeError, match="not created"):
        wait_for_socket(tmp_path / "missing.sock", timeout=0.1)
