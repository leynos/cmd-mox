"""Verify Unix server base selection without opening sockets.

Run with ``uv run pytest -q cmd_mox/unittests/test_unix_server_base.py``.
"""

import collections.abc as cabc
import socketserver

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc._unix_server_base import resolve_unix_server_base


class _StubUnixStreamServer(socketserver.BaseServer):
    """Stand-in Unix server base for testing the compatibility class."""


@pytest.fixture
def unix_server_base_cache() -> cabc.Iterator[None]:
    """Clear platform selection before and after each patched case."""
    resolve_unix_server_base.cache_clear()
    yield
    resolve_unix_server_base.cache_clear()


def test_windows_resolution_returns_unsupported_server(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """The Windows placeholder rejects construction without socket I/O."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    unsupported_server = resolve_unix_server_base()

    with pytest.raises(RuntimeError, match="unavailable on Windows"):
        unsupported_server(("localhost", 0), socketserver.StreamRequestHandler)


def test_resolution_uses_threading_unix_server_when_available(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Use the standard threaded server when the platform provides it."""
    threading_server = getattr(socketserver, "ThreadingUnixStreamServer", None)
    if threading_server is None:
        pytest.skip("ThreadingUnixStreamServer is unavailable")

    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    assert resolve_unix_server_base() is threading_server, (
        "ThreadingUnixStreamServer should be selected when available"
    )


def test_resolution_builds_threaded_compatibility_server(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Add threading when only the basic Unix server class is available."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        socketserver,
        "UnixStreamServer",
        _StubUnixStreamServer,
        raising=False,
    )

    compat_server = resolve_unix_server_base()

    assert issubclass(compat_server, socketserver.ThreadingMixIn), (
        "Compatibility server must add ThreadingMixIn"
    )
    assert issubclass(compat_server, _StubUnixStreamServer), (
        "Compatibility server must extend the available Unix server"
    )


def test_resolution_raises_without_unix_server_classes(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Raise when neither threaded nor basic Unix servers are available."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        socketserver,
        "UnixStreamServer",
        None,
        raising=False,
    )

    with pytest.raises(RuntimeError, match="not supported on this platform"):
        resolve_unix_server_base()
