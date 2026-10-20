"""Verify Unix server base selection without opening sockets.

Run with ``uv run pytest -q cmd_mox/unittests/test_unix_server_base.py``.
"""

import collections.abc as cabc
import socketserver

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc import _observability, _unix_server_base
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


def test_windows_resolution_returns_unsupported_server(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """The Windows placeholder rejects construction without socket I/O."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    with _observability.capture_events() as events:
        unsupported_server = resolve_unix_server_base()

    with pytest.raises(RuntimeError, match="unavailable on Windows"):
        unsupported_server(("localhost", 0), socketserver.StreamRequestHandler)

    assert [(event.outcome, event.error_category) for event in events] == [
        ("windows_unsupported", "RuntimeError"),
    ]


def test_resolution_uses_threading_unix_server_when_available(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Use the standard threaded server when the platform provides it."""
    threading_server = getattr(socketserver, "ThreadingUnixStreamServer", None)
    if threading_server is None:
        pytest.skip("ThreadingUnixStreamServer is unavailable")

    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    with _observability.capture_events() as events:
        assert resolve_unix_server_base() is threading_server, (
            "ThreadingUnixStreamServer should be selected when available"
        )

    assert [event.outcome for event in events] == ["native"]


def test_resolution_reuses_cached_base_after_platform_changes(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Keep the first resolved base class for the process lifetime."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    first_base = resolve_unix_server_base()

    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)

    assert resolve_unix_server_base() is first_base


def test_inner_server_class_is_cached_and_reports_cache_outcomes(
    monkeypatch: pytest.MonkeyPatch,
    unix_server_base_cache: None,
) -> None:
    """Reuse the generated class and count bounded cache outcomes."""
    resolver_calls = 0
    resolved_bases = iter((_StubUnixStreamServer, socketserver.TCPServer))

    def resolve_base() -> type[socketserver.BaseServer]:
        nonlocal resolver_calls
        resolver_calls += 1
        return next(resolved_bases)

    monkeypatch.setattr(_unix_server_base, "_resolve_unix_server_base", resolve_base)
    previous_counts = _observability.counter_snapshot()

    with _observability.capture_events() as events:
        first_class = unix_server._inner_server_cls_for_backend()
        second_class = unix_server._inner_server_cls_for_backend()

    assert second_class is first_class
    assert resolver_calls == 1
    assert [(event.operation, event.transport, event.outcome) for event in events] == [
        ("ipc.unix_server_base.cache", "unix", "miss"),
        ("ipc.unix_server_factory.cache", "unix", "miss"),
        ("ipc.unix_server_base.cache", "unix", "hit"),
        ("ipc.unix_server_factory.cache", "unix", "hit"),
    ]
    updated_counts = _observability.counter_snapshot()
    for event in events:
        assert updated_counts[event.key()] == previous_counts.get(event.key(), 0) + 1
        assert set(event.as_extra()) <= {
            "operation",
            "transport",
            "outcome",
            "error_category",
        }


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
        compat_server = resolve_unix_server_base()

    assert issubclass(compat_server, socketserver.ThreadingMixIn), (
        "Compatibility server must add ThreadingMixIn"
    )
    assert issubclass(compat_server, _StubUnixStreamServer), (
        "Compatibility server must extend the available Unix server"
    )
    assert [event.outcome for event in events] == ["compatibility"]


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

    with (
        _observability.capture_events() as events,
        pytest.raises(RuntimeError, match="not supported on this platform"),
    ):
        resolve_unix_server_base()

    assert [(event.outcome, event.error_category) for event in events] == [
        ("unsupported", "RuntimeError"),
    ]
