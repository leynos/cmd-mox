"""Verify Unix server base selection without opening sockets.

Run with ``uv run pytest -q cmd_mox/unittests/test_unix_server_base.py``.
"""

import collections.abc as cabc
import socketserver

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc import _unix_server_base
from cmd_mox.ipc import server as unix_server
from cmd_mox.ipc._unix_server_base import resolve_unix_server_base


class _StubUnixStreamServer(socketserver.BaseServer):
    """Stand-in Unix server base for testing the compatibility class."""


@pytest.fixture
def unix_server_base_cache() -> cabc.Iterator[None]:
    """Clear both lazy server caches around patched platform cases."""
    resolve_unix_server_base.cache_clear()
    unix_server._inner_server_cls.cache_clear()
    yield
    unix_server._inner_server_cls.cache_clear()
    resolve_unix_server_base.cache_clear()


@pytest.fixture
def unsupported_unix_server_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch away both Unix server classes for unsupported-platform tests."""
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


def test_resolution_reuses_cached_base_after_platform_changes(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Keep the first resolved base class for the process lifetime."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    first_base = resolve_unix_server_base()

    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)

    assert resolve_unix_server_base() is first_base, (
        "Platform changes must not replace the cached server base"
    )


def test_inner_server_class_is_cached(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Reuse the generated class and resolve its platform base only once."""
    resolver_calls = 0

    def resolve_base() -> type[socketserver.BaseServer]:
        nonlocal resolver_calls
        resolver_calls += 1
        return _StubUnixStreamServer

    monkeypatch.setattr(_unix_server_base, "_resolve_unix_server_base", resolve_base)

    first_class = unix_server._inner_server_cls()
    second_class = unix_server._inner_server_cls()

    assert second_class is first_class, (
        "The server factory must return its cached class"
    )
    assert resolver_calls == 1, "The base resolver must run only once"


def test_failed_resolution_is_not_cached(
    unix_server_base_cache: None,
    unsupported_unix_server_platform: None,
) -> None:
    """Retry resolution after a platform without Unix servers fails."""
    for _ in range(2):
        with pytest.raises(RuntimeError, match="not supported on this platform"):
            resolve_unix_server_base()

    assert resolve_unix_server_base.cache_info().misses == 2, (
        "A failed resolution must not be retained by the cache"
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
