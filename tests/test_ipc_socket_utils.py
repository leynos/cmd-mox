"""Tests for IPC socket utility helpers."""

from __future__ import annotations

import pathlib
import socket
import threading
import time

import pytest

from cmd_mox.ipc.socket_utils import cleanup_stale_socket, wait_for_socket

pytestmark = pytest.mark.requires_unix_sockets


def test_cleanup_stale_socket_noop_for_missing_path(tmp_path: pathlib.Path) -> None:
    """Non-existent socket paths should be ignored gracefully."""
    cleanup_stale_socket(tmp_path / "absent.sock")


def test_cleanup_stale_socket_removes_unbound_file(tmp_path: pathlib.Path) -> None:
    """cleanup_stale_socket should unlink orphaned socket files."""
    socket_path = tmp_path / "ipc.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.close()

    assert socket_path.exists()

    cleanup_stale_socket(socket_path)

    assert not socket_path.exists()


def test_cleanup_stale_socket_refuses_active_socket(tmp_path: pathlib.Path) -> None:
    """cleanup_stale_socket should not remove sockets with active listeners."""
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


def test_wait_for_socket_succeeds_when_server_accepts(tmp_path: pathlib.Path) -> None:
    """wait_for_socket should connect successfully once the server listens."""
    socket_path = tmp_path / "ipc.sock"

    def _serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            # Delay binding slightly so wait_for_socket exercises retry logic.
            time.sleep(0.05)
            server.bind(str(socket_path))
            server.listen()
            conn, _ = server.accept()
            conn.close()

    thread = threading.Thread(target=_serve)
    thread.start()
    try:
        wait_for_socket(socket_path, timeout=1.0)
    finally:
        thread.join()
        if socket_path.exists():
            socket_path.unlink()


def test_wait_for_socket_times_out(tmp_path: pathlib.Path) -> None:
    """wait_for_socket should raise when the socket never accepts connections."""
    with pytest.raises(RuntimeError, match="not accepting connections"):
        wait_for_socket(tmp_path / "missing.sock", timeout=0.1)


def test_wait_for_socket_retries_until_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """The polling loop should retry after transient connection failures."""
    attempts: list[int] = [0]

    class _FakeSocket:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def settimeout(self, _timeout: float) -> None:
            pass

        def connect(self, _address: str) -> None:
            if attempts[0] < 2:
                attempts[0] += 1
                raise FileNotFoundError("missing")
            attempts[0] += 1

        def close(self) -> None:  # pragma: no cover - closing is a no-op
            pass

        def __enter__(self) -> _FakeSocket:
            return self

        def __exit__(self, *_exc: object) -> None:
            self.close()

    monkeypatch.setattr("cmd_mox.ipc.socket_utils.socket.socket", _FakeSocket)
    monkeypatch.setattr("cmd_mox.ipc.socket_utils.time.sleep", lambda _duration: None)

    wait_for_socket(pathlib.Path("fake.sock"), timeout=0.1)
    assert attempts[0] == 3
