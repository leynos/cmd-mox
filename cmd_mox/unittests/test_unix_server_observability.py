"""Verify Unix backend observability without opening sockets."""

from __future__ import annotations

import logging
import socketserver
import typing as typ

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc import _observability
from cmd_mox.ipc import server as unix_server
from cmd_mox.ipc._unix_server_base import resolve_unix_server_base

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion


class _BackendFailureCase(typ.NamedTuple):
    """Pair a construction exception with its bounded telemetry category."""

    exception_type: type[Exception]
    expected_category: str


def _single_selection(
    events: cabc.Iterable[_observability.IPCEvent],
) -> _observability.IPCEvent:
    """Return the sole base-selection event in *events*.

    Returns
    -------
    _observability.IPCEvent
        The selected base event.
    """
    [selection] = [
        event
        for event in events
        if event.operation == unix_server.BACKEND_SELECTION_OPERATION
        and event.outcome == "selected"
    ]
    return selection


def test_direct_base_resolution_emits_no_observability(
    unix_server_base_cache: None,
    native_unix_server_platform: None,
    stub_unix_stream_server: type[socketserver.BaseServer],
) -> None:
    """Keep telemetry at backend creation rather than in the pure resolver."""
    with _observability.capture_events() as events:
        base_server = resolve_unix_server_base()

    assert base_server is stub_unix_stream_server, (
        "The configured base was not returned"
    )
    assert events == [], "Direct resolution must remain free of telemetry side effects"


def test_direct_inner_server_factory_emits_no_observability(
    unix_server_base_cache: None,
    native_unix_server_platform: None,
    stub_unix_stream_server: type[socketserver.BaseServer],
) -> None:
    """Keep the cached class factory free of backend telemetry side effects."""
    with _observability.capture_events() as events:
        inner_server_cls = unix_server._inner_server_cls()

    assert issubclass(
        typ.cast("type[socketserver.BaseServer]", inner_server_cls),
        stub_unix_stream_server,
    ), "The factory must subclass the resolved platform base"
    assert events == [], "Direct factory access must not emit backend telemetry"


def test_direct_inner_server_factory_failure_emits_no_observability(
    unix_server_base_cache: None,
    unsupported_unix_server_platform: None,
) -> None:
    """Propagate resolution errors without emitting from the cached factory."""
    with (
        _observability.capture_events() as events,
        pytest.raises(RuntimeError, match="not supported on this platform"),
    ):
        unix_server._inner_server_cls()

    assert events == [], "Direct factory failure must not emit backend telemetry"


def test_resolution_builds_threaded_compatibility_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unix_server_base_cache: None,
    stub_unix_stream_server: type[socketserver.BaseServer],
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
        stub_unix_stream_server,
        raising=False,
    )

    server = unix_server.IPCServer(tmp_path / "unused.sock")
    with _observability.capture_events() as events:
        server._create_backend()

    compat_server = resolve_unix_server_base()
    assert issubclass(compat_server, socketserver.ThreadingMixIn), (
        "Compatibility server must add ThreadingMixIn"
    )
    assert issubclass(compat_server, stub_unix_stream_server), (
        "Compatibility server must extend the available Unix server"
    )
    selection = _single_selection(events)
    assert selection.selection_mode == "compatibility", (
        "The fallback choice must be observable"
    )
    assert selection.cache_name == "unix_server_base", "Wrong cache was reported"
    assert selection.cache_outcome == "miss", "First resolution must miss"
    class_cache_events = [
        event
        for event in events
        if event.cache_name == "inner_server_class" and event.outcome == "cache_access"
    ]
    assert [event.cache_outcome for event in class_cache_events] == ["miss"], (
        "First server-class creation must miss"
    )


def test_server_class_cache_hit_is_observable(
    unix_server_base_cache: None,
    native_unix_server_platform: None,
) -> None:
    """Record reuse of the cached server class without opening a socket."""
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
    selection = _single_selection(events)
    assert selection.selection_mode == "native", "The native choice must be observed"
    assert selection.cache_outcome == "miss", "The first base lookup must miss"


def test_unix_base_cache_hit_is_observable_after_factory_reset(
    unix_server_base_cache: None,
    native_unix_server_platform: None,
) -> None:
    """Report reuse of the base class when the inner class is rebuilt."""
    unix_server._inner_server_cls_for_backend()
    unix_server._inner_server_cls.cache_clear()

    with _observability.capture_events() as events:
        unix_server._inner_server_cls_for_backend()

    selection = _single_selection(events)
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
    "case",
    [
        pytest.param(_BackendFailureCase(OSError, "os_error"), id="os-error"),
        pytest.param(
            _BackendFailureCase(RuntimeError, "runtime_error"),
            id="runtime-error",
        ),
        pytest.param(_BackendFailureCase(TypeError, "type_error"), id="type-error"),
        pytest.param(_BackendFailureCase(ValueError, "value_error"), id="value-error"),
    ],
)
def test_backend_construction_failure_is_observable_without_socket_io(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    unix_server_base_cache: None,
    case: _BackendFailureCase,
) -> None:
    """Map backend errors to fixed categories without retaining messages."""
    test_marker = "backend construction detail"
    exception_type = case.exception_type

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
    assert failure.error_category == case.expected_category, "Wrong error category"
    assert test_marker not in str(failure.as_extra()), (
        "Exception messages must not enter telemetry"
    )


def _selection_events_for_snapshot() -> dict[str, _observability.IPCEvent]:
    """Return backend-selection events for structured-log assertions.

    Returns
    -------
    dict[str, _observability.IPCEvent]
        Events keyed by the scenario represented in each structured log.
    """
    operation = unix_server.BACKEND_SELECTION_OPERATION
    return {
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
    }


def _failure_events_for_snapshot() -> dict[str, _observability.IPCEvent]:
    """Return backend-failure events without caller-controlled details.

    Returns
    -------
    dict[str, _observability.IPCEvent]
        Failure events keyed by the backend outcome they exercise.
    """
    operation = unix_server.BACKEND_SELECTION_OPERATION
    return {
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


def _logged_backend_event_payloads(
    events: dict[str, _observability.IPCEvent],
    caplog: pytest.LogCaptureFixture,
) -> cabc.Mapping[str, cabc.Mapping[str, object]]:
    """Capture emitted messages and structured fields for snapshot assertions.

    Returns
    -------
    Mapping[str, Mapping[str, object]]
        Captured messages and structured fields, keyed by event scenario.
    """
    caplog.clear()
    _observability.reset_counters()
    try:
        with caplog.at_level(logging.INFO, logger="cmd_mox.ipc._observability"):
            for event in events.values():
                _observability.emit(event)

        return {
            name: {
                "message": record.getMessage(),
                "extra": {field: getattr(record, field) for field in event.as_extra()},
            }
            for (name, event), record in zip(
                events.items(), caplog.records, strict=True
            )
        }
    finally:
        _observability.reset_counters()


def test_backend_event_payloads_match_snapshot(
    snapshot: SnapshotAssertion, caplog: pytest.LogCaptureFixture
) -> None:
    """Keep structured backend log output stable and free of dynamic data."""
    events = {**_selection_events_for_snapshot(), **_failure_events_for_snapshot()}
    logged_events = _logged_backend_event_payloads(events, caplog)

    assert logged_events == snapshot, "Structured backend log fields changed"
