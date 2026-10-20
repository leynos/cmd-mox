"""Verify Unix server base selection without opening sockets.

Run with ``uv run pytest -q cmd_mox/unittests/test_unix_server_base.py``.
"""

from __future__ import annotations

import concurrent.futures
import logging
import socketserver
import threading
import typing as typ

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc import _observability, _unix_server_base
from cmd_mox.ipc import server as unix_server
from cmd_mox.ipc._unix_server_base import resolve_unix_server_base

if typ.TYPE_CHECKING:
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion


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


def test_direct_base_resolution_emits_no_observability(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Keep telemetry at backend creation rather than in the pure resolver."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        _StubUnixStreamServer,
        raising=False,
    )

    with _observability.capture_events() as events:
        base_server = resolve_unix_server_base()

    assert base_server is _StubUnixStreamServer, "The configured base was not returned"
    assert events == [], "Direct resolution must remain free of telemetry side effects"


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
    worker_count = 4
    resolver_started = threading.Event()
    release_resolver = threading.Event()
    start_access = threading.Barrier(worker_count + 1)
    resolver_calls = 0
    tracked_lock = _TrackedLock(worker_count)
    monkeypatch.setattr(unix_server, "_INNER_SERVER_CLASS_CACHE_LOCK", tracked_lock)

    def resolve_base() -> type[socketserver.BaseServer]:
        nonlocal resolver_calls
        resolver_calls += 1
        resolver_started.set()
        if not release_resolver.wait(timeout=5):
            raise TimeoutError
        return _StubUnixStreamServer

    monkeypatch.setattr(_unix_server_base, "_resolve_unix_server_base", resolve_base)

    def get_server_class() -> type[socketserver.BaseServer]:
        start_access.wait(timeout=5)
        server_class, _cache_outcome = unix_server._inner_server_cls_for_backend()
        return typ.cast("type[socketserver.BaseServer]", server_class)

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(get_server_class) for _ in range(worker_count)]
        try:
            start_access.wait(timeout=5)
            assert resolver_started.wait(timeout=5), "No worker entered the resolver"
            assert tracked_lock.all_workers_waiting.wait(timeout=5), (
                "Workers did not contend on the server-class cache lock"
            )
        finally:
            release_resolver.set()
        server_classes = [future.result(timeout=5) for future in futures]

    assert resolver_calls == 1, "Concurrent first access must resolve the base once"
    assert all(server_class is server_classes[0] for server_class in server_classes), (
        "Concurrent callers must share one generated class"
    )


def test_concurrent_unix_base_access_resolves_once(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Serialize concurrent first access to the cached Unix base resolver."""
    worker_count = 4
    resolver_started = threading.Event()
    release_resolver = threading.Event()
    start_access = threading.Barrier(worker_count + 1)
    resolver_calls = 0
    tracked_lock = _TrackedLock(worker_count)
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        _StubUnixStreamServer,
        raising=False,
    )
    monkeypatch.setattr(unix_server, "_UNIX_SERVER_BASE_CACHE_LOCK", tracked_lock)

    def resolve_base() -> type[socketserver.BaseServer]:
        nonlocal resolver_calls
        resolver_calls += 1
        resolver_started.set()
        if not release_resolver.wait(timeout=5):
            raise TimeoutError
        return _StubUnixStreamServer

    monkeypatch.setattr(_unix_server_base, "_resolve_unix_server_base", resolve_base)

    def get_server_base() -> tuple[type[socketserver.BaseServer], str]:
        start_access.wait(timeout=5)
        return unix_server._resolve_unix_server_base_for_inner_server()

    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(get_server_base) for _ in range(worker_count)]
        try:
            start_access.wait(timeout=5)
            assert resolver_started.wait(timeout=5), "No worker entered the resolver"
            assert tracked_lock.all_workers_waiting.wait(timeout=5), (
                "Workers did not contend on the Unix-base cache lock"
            )
        finally:
            release_resolver.set()
        server_bases = [future.result(timeout=5) for future in futures]

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

    with _observability.capture_events() as events:
        _inner_server_cls, cache_outcome = unix_server._inner_server_cls_for_backend()

    compat_server = resolve_unix_server_base()
    assert issubclass(compat_server, socketserver.ThreadingMixIn), (
        "Compatibility server must add ThreadingMixIn"
    )
    assert issubclass(compat_server, _StubUnixStreamServer), (
        "Compatibility server must extend the available Unix server"
    )
    [selection] = [
        event
        for event in events
        if event.operation == unix_server.BACKEND_SELECTION_OPERATION
        and event.outcome == "selected"
    ]
    assert selection.selection_mode == "compatibility", (
        "The fallback choice must be observable"
    )
    assert selection.cache_name == "unix_server_base", "Wrong cache was reported"
    assert selection.cache_outcome == "miss", "First resolution must miss"
    assert cache_outcome == "miss", "First server-class creation must miss"


def test_server_class_cache_hit_is_observable(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Record reuse of the cached server class without opening a socket."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        _StubUnixStreamServer,
        raising=False,
    )

    with _observability.capture_events() as events:
        first_class, first_outcome = unix_server._inner_server_cls_for_backend()
        second_class, second_outcome = unix_server._inner_server_cls_for_backend()

    assert second_class is first_class, "The class factory must remain cached"
    assert (first_outcome, second_outcome) == ("miss", "hit"), (
        "The factory cache outcome must be reported"
    )
    class_cache_events = [
        event
        for event in events
        if event.operation == unix_server.BACKEND_SELECTION_OPERATION
        and event.cache_name == "inner_server_class"
    ]
    assert [event.cache_outcome for event in class_cache_events] == ["miss", "hit"], (
        "Each inner-class lookup must report its cache outcome"
    )
    [selection] = [
        event
        for event in events
        if event.operation == unix_server.BACKEND_SELECTION_OPERATION
        and event.outcome == "selected"
    ]
    assert selection.selection_mode == "native", "The native choice must be observed"
    assert selection.cache_outcome == "miss", "The first base lookup must miss"


def test_unix_base_cache_hit_is_observable_after_factory_reset(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Report reuse of the base class when the inner class is rebuilt."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        _StubUnixStreamServer,
        raising=False,
    )
    unix_server._inner_server_cls_for_backend()
    unix_server._inner_server_cls.cache_clear()

    with _observability.capture_events() as events:
        unix_server._inner_server_cls_for_backend()

    [selection] = [
        event
        for event in events
        if event.operation == unix_server.BACKEND_SELECTION_OPERATION
        and event.outcome == "selected"
    ]
    assert selection.selection_mode == "native", "The native choice must be observed"
    assert selection.cache_name == "unix_server_base", "Wrong cache was reported"
    assert selection.cache_outcome == "hit", "The base class should be reused"


def test_windows_backend_rejection_is_observable_without_socket_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unix_server_base_cache: None,
) -> None:
    """Record the bounded Windows rejection at backend construction."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    server = unix_server.IPCServer(tmp_path / "unused.sock")

    with (
        _observability.capture_events() as events,
        pytest.raises(RuntimeError, match="unavailable on Windows"),
    ):
        server._create_backend()

    [rejection] = [
        event
        for event in events
        if event.operation == unix_server.BACKEND_SELECTION_OPERATION
        and event.outcome == "windows_rejected"
    ]
    assert rejection.selection_mode == "windows_unsupported", "Wrong selection mode"
    assert rejection.cache_outcome == "miss", "The first class lookup must miss"
    assert rejection.error_category == "windows_unsupported", "Wrong error category"


def test_unsupported_platform_failure_is_observable_without_socket_io(
    tmp_path: Path,
    unix_server_base_cache: None,
    unsupported_unix_server_platform: None,
) -> None:
    """Record failed Unix-base resolution using bounded fields only."""
    server = unix_server.IPCServer(tmp_path / "unused.sock")

    with (
        _observability.capture_events() as events,
        pytest.raises(RuntimeError, match="not supported on this platform"),
    ):
        server._create_backend()

    backend_events = [
        event
        for event in events
        if event.operation == unix_server.BACKEND_SELECTION_OPERATION
    ]
    assert len(backend_events) == 1, "A failed resolution must emit exactly one event"
    [failure] = backend_events
    assert failure.outcome == "resolution_failed", "Wrong failure outcome"
    assert failure.selection_mode == "unsupported_platform", "Wrong failure mode"
    assert failure.cache_name == "unix_server_base", "Wrong cache was reported"
    assert failure.cache_outcome == "miss", "A failed lookup is a cache miss"
    assert failure.error_category == "unsupported_platform", "Wrong error category"


@pytest.mark.parametrize(
    ("exception_type", "expected_category"),
    [
        (OSError, "os_error"),
        (RuntimeError, "runtime_error"),
        (TypeError, "type_error"),
        (ValueError, "value_error"),
    ],
    ids=["os-error", "runtime-error", "type-error", "value-error"],
)
def test_backend_construction_failure_is_observable_without_socket_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unix_server_base_cache: None,
    exception_type: type[Exception],
    expected_category: str,
) -> None:
    """Map backend errors to fixed categories without retaining messages."""
    test_marker = "backend construction detail"

    class _FailingUnixStreamServer(socketserver.BaseServer):
        """Base-server double that fails before opening a socket."""

        def __init__(
            self,
            server_address: object,
            _request_handler_class: object,
        ) -> None:
            del server_address
            raise exception_type(test_marker)

    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        _FailingUnixStreamServer,
        raising=False,
    )
    server = unix_server.IPCServer(tmp_path / "unused.sock")

    with (
        _observability.capture_events() as events,
        pytest.raises(exception_type, match=test_marker),
    ):
        server._create_backend()

    [failure] = [
        event
        for event in events
        if event.operation == unix_server.BACKEND_SELECTION_OPERATION
        and event.outcome == "backend_construction_failed"
    ]
    assert failure.selection_mode == "native", "Wrong selection mode"
    assert failure.error_category == expected_category, "Wrong error category"
    assert test_marker not in str(failure.as_extra()), (
        "Exception messages must not enter telemetry"
    )


def test_backend_event_payloads_match_snapshot(
    snapshot: SnapshotAssertion, caplog: pytest.LogCaptureFixture
) -> None:
    """Keep structured backend log output stable and free of dynamic data."""
    operation = unix_server.BACKEND_SELECTION_OPERATION
    events = {
        "native_base_miss": _observability.IPCEvent(
            operation=operation,
            transport="unix",
            selection_mode="native",
            outcome="selected",
            cache_name="unix_server_base",
            cache_outcome="miss",
        ),
        "compatibility_base_hit": _observability.IPCEvent(
            operation=operation,
            transport="unix",
            selection_mode="compatibility",
            outcome="selected",
            cache_name="unix_server_base",
            cache_outcome="hit",
        ),
        "inner_class_cache_miss": _observability.IPCEvent(
            operation=operation,
            transport="unix",
            selection_mode="native",
            outcome="cache_access",
            cache_name="inner_server_class",
            cache_outcome="miss",
        ),
        "inner_class_cache_hit": _observability.IPCEvent(
            operation=operation,
            transport="unix",
            selection_mode="native",
            outcome="cache_access",
            cache_name="inner_server_class",
            cache_outcome="hit",
        ),
        "windows_rejected": _observability.IPCEvent(
            operation=operation,
            transport="unix",
            selection_mode="windows_unsupported",
            outcome="windows_rejected",
            cache_name="inner_server_class",
            cache_outcome="miss",
            error_category="windows_unsupported",
        ),
        "unsupported_platform": _observability.IPCEvent(
            operation=operation,
            transport="unix",
            selection_mode="unsupported_platform",
            outcome="resolution_failed",
            cache_name="unix_server_base",
            cache_outcome="miss",
            error_category="unsupported_platform",
        ),
        "backend_construction_failed": _observability.IPCEvent(
            operation=operation,
            transport="unix",
            selection_mode="native",
            outcome="backend_construction_failed",
            cache_name="inner_server_class",
            cache_outcome="miss",
            error_category="os_error",
        ),
    }

    caplog.clear()
    _observability.reset_counters()
    try:
        with caplog.at_level(logging.INFO, logger="cmd_mox.ipc._observability"):
            for event in events.values():
                _observability.emit(event)

        logged_events = {
            name: {
                "message": record.getMessage(),
                "extra": {field: getattr(record, field) for field in event.as_extra()},
            }
            for (name, event), record in zip(
                events.items(), caplog.records, strict=True
            )
        }
        assert logged_events == snapshot, "Structured backend log fields changed"
    finally:
        _observability.reset_counters()
