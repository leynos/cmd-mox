"""Tests for IPC socket utility helpers."""

from __future__ import annotations

import pathlib
import socket
import threading
import time
import typing as typ

import pytest

from cmd_mox.ipc.socket_utils import cleanup_stale_socket, wait_for_socket

if typ.TYPE_CHECKING:
    import collections.abc as cabc

pytestmark = [pytest.mark.requires_unix_sockets]


@pytest.fixture
def bound_socket(
    tmp_path: pathlib.Path,
) -> cabc.Iterator[tuple[pathlib.Path, socket.socket]]:
    """Bind a Unix socket, yielding it with its path and cleaning up after.

    Yields
    ------
    tuple[pathlib.Path, socket.socket]
        The bound socket's path and the socket itself.
    """
    socket_path = tmp_path / "ipc.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    try:
        yield socket_path, server
    finally:
        server.close()
        if socket_path.exists():
            socket_path.unlink()


def test_cleanup_stale_socket_noop_for_missing_path(tmp_path: pathlib.Path) -> None:
    """Non-existent socket paths should be ignored gracefully."""
    absent = tmp_path / "absent.sock"

    cleanup_stale_socket(absent)

    assert not absent.exists(), "Missing socket path must not be created"


def test_cleanup_stale_socket_removes_unbound_file(
    bound_socket: tuple[pathlib.Path, socket.socket],
) -> None:
    """cleanup_stale_socket should unlink orphaned socket files."""
    socket_path, server = bound_socket
    server.close()

    assert socket_path.exists(), "binding should leave a socket file behind"

    cleanup_stale_socket(socket_path)

    assert not socket_path.exists(), "an orphaned socket file should be unlinked"


def test_cleanup_stale_socket_refuses_active_socket(
    bound_socket: tuple[pathlib.Path, socket.socket],
) -> None:
    """cleanup_stale_socket should not remove sockets with active listeners."""
    socket_path, server = bound_socket
    server.listen()

    with pytest.raises(RuntimeError, match="still in use"):
        cleanup_stale_socket(socket_path)

    assert socket_path.exists(), "an active socket must not be unlinked"


def test_wait_for_socket_succeeds_when_server_accepts(tmp_path: pathlib.Path) -> None:
    """wait_for_socket should connect successfully once the server listens."""
    socket_path = tmp_path / "ipc.sock"
    accepted: list[bool] = []

    def _serve() -> None:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            # Delay binding slightly so wait_for_socket exercises retry logic.
            time.sleep(0.05)
            server.bind(str(socket_path))
            server.listen()
            conn, _ = server.accept()
            accepted.append(True)
            conn.close()

    thread = threading.Thread(target=_serve)
    thread.start()
    try:
        wait_for_socket(socket_path, timeout=1.0)
    finally:
        thread.join()
        if socket_path.exists():
            socket_path.unlink()

    assert accepted == [True], "wait_for_socket did not connect to the listener"


def test_wait_for_socket_times_out(tmp_path: pathlib.Path) -> None:
    """wait_for_socket should raise when the socket never accepts connections."""
    with pytest.raises(RuntimeError, match="not accepting connections"):
        wait_for_socket(tmp_path / "missing.sock", timeout=0.1)


def test_wait_for_socket_retries_until_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    assert attempts[0] == 3, "Assertion failed"


def test_cleanup_stale_socket_keeps_an_unreachable_socket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """A probe denied by permissions must not delete a possibly live socket."""
    socket_path = tmp_path / "ipc.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.close()

    def _refuse(_self: object, _address: str) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(socket.socket, "connect", _refuse)

    with pytest.raises(PermissionError):
        cleanup_stale_socket(socket_path)

    assert socket_path.exists(), "an unreachable socket must not be unlinked"
