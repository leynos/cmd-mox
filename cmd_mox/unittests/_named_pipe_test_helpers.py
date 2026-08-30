"""Shared helpers for the named-pipe transport's bounds tests.

The admission and read-bounds suites both need to inspect the bounded worker
events the transport emits and to drive a single client through a worker
thread. Those helpers live here so each suite can stay focused on one
production responsibility.
"""

from __future__ import annotations

import typing as typ

from cmd_mox.ipc._named_pipe_limits import (
    WORKER_OPERATION,
    ClientSlot,
    acquire_client_slot,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from cmd_mox.ipc import _observability, named_pipe

#: Fields an event may never carry: they would be derived from the payload.
_FORBIDDEN_FIELDS: typ.Final[tuple[str, ...]] = (
    "kind",
    "correlation_id",
    "attempt",
)


def _worker_events(
    events: cabc.Sequence[_observability.IPCEvent], outcome: str
) -> list[_observability.IPCEvent]:
    """Return captured worker events matching *outcome*.

    Returns
    -------
    list[cmd_mox.ipc._observability.IPCEvent]
        The matching events, in emission order.
    """
    return [
        event
        for event in events
        if event.operation == WORKER_OPERATION and event.outcome == outcome
    ]


def _assert_bounded(event: _observability.IPCEvent) -> None:
    """Assert *event* carries no payload-derived dimension."""
    assert event.transport == "named_pipe", (  # ruff: ignore[assert] - shared test assertion helper
        "transport dimension missing"
    )
    for field in _FORBIDDEN_FIELDS:
        assert getattr(event, field) is None, (  # ruff: ignore[assert] - shared test assertion helper
            f"{field} must not be set"
        )


def _run_worker(state: named_pipe._NamedPipeState, handle: object) -> ClientSlot:
    """Serve one client on a worker thread and wait for it to finish.

    Asserts that a permit was available and that the worker thread finished.

    Returns
    -------
    ClientSlot
        The permit handed to the worker, so callers can assert on its release.
    """
    slot = acquire_client_slot(state._client_slots)
    assert slot is not None, "no permit was available"  # ruff: ignore[assert] - shared test assertion helper
    state._spawn_handler_thread(handle, slot)
    for thread in state._get_active_threads():
        thread.join(5.0)
        assert not thread.is_alive(), (  # ruff: ignore[assert] - shared test assertion helper
            "worker thread did not finish"
        )
    return slot
