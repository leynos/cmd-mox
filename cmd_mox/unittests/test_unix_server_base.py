"""Verify Unix server base selection without opening sockets.

Run with ``uv run pytest -q cmd_mox/unittests/test_unix_server_base.py``.
"""

from __future__ import annotations

import concurrent.futures
import socketserver
import threading
import typing as typ

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc import _unix_server_base
from cmd_mox.ipc import server as unix_server
from cmd_mox.ipc._unix_server_base import resolve_unix_server_base

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path


class _StubUnixStreamServer(socketserver.BaseServer):
    """Stand-in Unix server base for testing the compatibility class."""


class _ServerWithThreadSettings(typ.Protocol):
    """Generated backend attributes needed by lifecycle assertions."""

    outer: unix_server.IPCServer
    daemon_threads: bool


class _TrackedLock:
    """Signal when every test worker has attempted a guarded cache access."""

    def __init__(self, expected_attempts: int) -> None:
        self._lock = threading.Lock()
        self._attempt_lock = threading.Lock()
        self._expected_attempts = expected_attempts
        self._attempt_count = 0
        self.all_workers_waiting = threading.Event()

    def __enter__(self) -> _TrackedLock:
        with self._attempt_lock:
            self._attempt_count += 1
            if self._attempt_count == self._expected_attempts:
                self.all_workers_waiting.set()
        self._lock.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self._lock.release()


def _run_concurrent_first_access[T](
    monkeypatch: pytest.MonkeyPatch,
    cache_lock: tuple[object, str],
    access: cabc.Callable[[], T],
    lock_description: str,
) -> tuple[int, list[T]]:
    """Verify that concurrent first access invokes the resolver once.

    Returns
    -------
    tuple[int, list[T]]
        Resolver invocation count and values returned to each worker.
    """
    worker_count = 4
    resolver_started = threading.Event()
    release_resolver = threading.Event()
    start_access = threading.Barrier(worker_count + 1)
    resolver_calls = 0
    tracked_lock = _TrackedLock(worker_count)
    cache_lock_owner, cache_lock_name = cache_lock
    monkeypatch.setattr(cache_lock_owner, cache_lock_name, tracked_lock)

    def resolve_base() -> type[socketserver.BaseServer]:
        nonlocal resolver_calls
        resolver_calls += 1
        resolver_started.set()
        if not release_resolver.wait(timeout=5):
            raise TimeoutError
        return _StubUnixStreamServer

    monkeypatch.setattr(_unix_server_base, "_resolve_unix_server_base", resolve_base)

    def access_cache() -> T:
        start_access.wait(timeout=5)
        return access()

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(access_cache) for _ in range(worker_count)]
        try:
            start_access.wait(timeout=5)
            assert resolver_started.wait(timeout=5), "No worker entered the resolver"
            assert tracked_lock.all_workers_waiting.wait(timeout=5), (
                f"Workers did not contend on the {lock_description} cache lock"
            )
        finally:
            release_resolver.set()
        results = [future.result(timeout=5) for future in futures]

    return resolver_calls, results


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


def test_generated_server_class_keeps_daemon_threads_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unix_server_base_cache: None,
) -> None:
    """Retain threaded lifecycle settings without opening a listening socket."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        _StubUnixStreamServer,
        raising=False,
    )
    outer_server = unix_server.IPCServer(tmp_path / "unused.sock")

    generated_server = typ.cast(
        "_ServerWithThreadSettings",
        unix_server._inner_server_cls()(outer_server.socket_path, outer_server),
    )

    assert generated_server.outer is outer_server, "The outer server was not attached"
    assert generated_server.daemon_threads is True, (
        "Worker threads must remain daemonized"
    )


def test_concurrent_server_class_access_resolves_once(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Serialize concurrent first access to the cached backend class."""
    resolver_calls, server_classes = _run_concurrent_first_access(
        monkeypatch,
        (unix_server, "_INNER_SERVER_CLASS_CACHE_LOCK"),
        lambda: unix_server._inner_server_cls_for_backend()[0],
        "server-class",
    )

    assert resolver_calls == 1, "Concurrent first access must resolve the base once"
    assert all(server_class is server_classes[0] for server_class in server_classes), (
        "Concurrent callers must share one generated class"
    )


def test_concurrent_unix_base_access_resolves_once(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Serialize concurrent first access to the cached Unix base resolver."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        _StubUnixStreamServer,
        raising=False,
    )
    resolver_calls, server_bases = _run_concurrent_first_access(
        monkeypatch,
        (unix_server, "_UNIX_SERVER_BASE_CACHE_LOCK"),
        unix_server._resolve_unix_server_base_for_inner_server,
        "Unix-base",
    )

    assert resolver_calls == 1, "Concurrent first access must resolve the base once"
    assert all(
        server_base == (_StubUnixStreamServer, "native") for server_base in server_bases
    ), "Concurrent callers must share the same resolved base and mode"


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
