"""Verify Unix server base selection without opening sockets.

Run with ``uv run pytest -q cmd_mox/unittests/test_unix_server_base.py``.
"""

import socketserver
from pathlib import Path

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc import _observability, _unix_server_base
from cmd_mox.ipc import server as unix_server
from cmd_mox.ipc._unix_server_base import resolve_unix_server_base


class _StubUnixStreamServer(socketserver.BaseServer):
    """Stand-in Unix server base for testing the compatibility class."""


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

    [failure] = [
        event
        for event in events
        if event.operation == unix_server.BACKEND_SELECTION_OPERATION
        and event.outcome == "resolution_failed"
    ]
    assert failure.selection_mode == "unsupported_platform", "Wrong failure mode"
    assert failure.cache_name == "unix_server_base", "Wrong cache was reported"
    assert failure.cache_outcome == "miss", "A failed lookup is a cache miss"
    assert failure.error_category == "unsupported_platform", "Wrong error category"


def test_backend_construction_failure_is_observable_without_socket_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unix_server_base_cache: None,
) -> None:
    """Record construction errors without retaining their messages."""

    class _FailingUnixStreamServer(socketserver.BaseServer):
        """Base-server double that fails before opening a socket."""

        def __init__(
            self,
            server_address: object,
            _request_handler_class: object,
        ) -> None:
            message = f"secret backend path: {server_address!s}"
            raise OSError(message)

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
        pytest.raises(OSError, match="secret backend path"),
    ):
        server._create_backend()

    [failure] = [
        event
        for event in events
        if event.operation == unix_server.BACKEND_SELECTION_OPERATION
        and event.outcome == "backend_construction_failed"
    ]
    assert failure.selection_mode == "native", "Wrong selection mode"
    assert failure.error_category == "os_error", "Wrong error category"
    assert "secret backend path" not in str(failure.as_extra()), (
        "Exception messages must not enter telemetry"
    )
