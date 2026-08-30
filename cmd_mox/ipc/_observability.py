"""Bounded, dependency-free observability seam for the IPC layer.

Every IPC emitter routed through this module describes *what happened*, never
*what was transferred*. The following are never logged, in any field, by any
emitter:

* command names and command arguments;
* standard input, standard output, and standard error content;
* environment variable names or values;
* socket paths, named-pipe names, and any other filesystem location;
* exception messages, tracebacks, and object ``repr`` output;
* raw request or response payloads.

Any field added to :class:`IPCEvent` in future must be a **bounded dimension**:
a value drawn from a small closed set fixed by the source code (an operation
name, a transport name, an outcome, an exception class name), a numeric
measurement, or an opaque correlation identifier that is never derived from
request content. Free-form strings taken from a request must never be added.

The seam has no third-party dependencies. It uses the standard library
:mod:`logging` plus in-process counters, so tests can assert on emitted events
through :func:`capture_events` or :func:`counter_snapshot` without an external
metrics collector.

Counters are keyed by :class:`EventKey`, the ``(operation, transport,
outcome)`` triple that identifies an event *class*. Those three dimensions are
closed sets, so the counter registry stays bounded no matter how many requests
are processed.
"""

from __future__ import annotations

import collections
import contextlib
import dataclasses as dc
import logging
import threading
import time
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_LOGGER = logging.getLogger(__name__)

EVENT_MESSAGE: typ.Final[str] = "IPC observability event"

type Transport = typ.Literal["unix", "named_pipe"]
type EventField = str | int | float


class EventKey(typ.NamedTuple):
    """Bounded dimensions identifying one class of emitted event."""

    operation: str
    transport: str | None
    outcome: str | None


@dc.dataclass(frozen=True, slots=True)
class IPCEvent:
    """One bounded observability event describing an IPC operation.

    Parameters
    ----------
    operation:
        Dotted name of the operation being reported, e.g. ``"ipc.dispatch"``.
    transport:
        Transport carrying the request, when the emitter knows it.
    kind:
        Wire message kind, e.g. ``"invocation"``.
    outcome:
        Terminal state of the operation, e.g. ``"success"``.
    error_category:
        Exception class name or other closed-set failure label. Never an
        exception message.
    attempt:
        1-based attempt counter for retried operations.
    duration_ms:
        Measured duration in milliseconds.
    message_size:
        Size in bytes of the message the operation moved.
    correlation_id:
        Opaque identifier shared by the client and server records for one
        request. Never derived from request content.
    """

    operation: str
    transport: Transport | None = None
    kind: str | None = None
    outcome: str | None = None
    error_category: str | None = None
    attempt: int | None = None
    duration_ms: float | None = None
    message_size: int | None = None
    correlation_id: str | None = None

    def as_extra(self) -> dict[str, EventField]:
        """Render the event as structured logging fields.

        Fields that do not apply to the event are omitted rather than logged
        as ``None``.

        Returns
        -------
        dict[str, str | int | float]
            The bounded fields describing this event.
        """
        fields: dict[str, EventField | None] = {
            "operation": self.operation,
            "transport": self.transport,
            "kind": self.kind,
            "outcome": self.outcome,
            "error_category": self.error_category,
            "attempt": self.attempt,
            "duration_ms": self.duration_ms,
            "message_size": self.message_size,
            "correlation_id": self.correlation_id,
        }
        return {name: value for name, value in fields.items() if value is not None}

    def key(self) -> EventKey:
        """Return the bounded counter key for this event.

        Returns
        -------
        EventKey
            The ``(operation, transport, outcome)`` triple for this event.
        """
        return EventKey(self.operation, self.transport, self.outcome)


_LOCK: typ.Final[threading.Lock] = threading.Lock()
_COUNTERS: typ.Final[collections.Counter[EventKey]] = collections.Counter()
_CAPTURES: typ.Final[list[list[IPCEvent]]] = []


def emit(
    event: IPCEvent,
    *,
    logger: logging.Logger | None = None,
    extra: cabc.Mapping[str, EventField] | None = None,
    message: str = EVENT_MESSAGE,
) -> None:
    """Log *event* at INFO and count it in the in-process registry.

    Parameters
    ----------
    event:
        The bounded event to emit.
    logger:
        Logger to log through, so records stay attributed to the module whose
        behaviour they describe. Defaults to this module's logger.
    extra:
        Additional bounded fields the caller has already vetted against the
        never-log list. They are merged into the structured record but do not
        affect the counter key.
    message:
        Fixed log message. It must never interpolate event data.
    """
    fields = event.as_extra()
    if extra:
        fields.update(extra)
    (logger or _LOGGER).info(message, extra=fields)
    with _LOCK:
        _COUNTERS[event.key()] += 1
        for buffer in _CAPTURES:
            buffer.append(event)


def counter_snapshot() -> dict[EventKey, int]:
    """Return a copy of the in-process event counters.

    Returns
    -------
    dict[EventKey, int]
        Emission counts keyed by bounded event class.
    """
    with _LOCK:
        return dict(_COUNTERS)


def reset_counters() -> None:
    """Clear the in-process event counters."""
    with _LOCK:
        _COUNTERS.clear()


@contextlib.contextmanager
def capture_events() -> cabc.Iterator[list[IPCEvent]]:
    """Collect events emitted while the context is active.

    The yielded list is appended to as events are emitted, so tests can assert
    on emissions without an external metrics collector.

    Yields
    ------
    list[IPCEvent]
        The live buffer of captured events.
    """
    buffer: list[IPCEvent] = []
    with _LOCK:
        _CAPTURES.append(buffer)
    try:
        yield buffer
    finally:
        with _LOCK:
            _CAPTURES.remove(buffer)


def elapsed_ms(started: float) -> float:
    """Return milliseconds elapsed since the *started* monotonic reading.

    Parameters
    ----------
    started:
        A :func:`time.perf_counter` reading taken when the operation began.

    Returns
    -------
    float
        Elapsed duration in milliseconds.
    """
    return (time.perf_counter() - started) * 1000.0


__all__ = [
    "EVENT_MESSAGE",
    "EventField",
    "EventKey",
    "IPCEvent",
    "Transport",
    "capture_events",
    "counter_snapshot",
    "elapsed_ms",
    "emit",
    "reset_counters",
]
