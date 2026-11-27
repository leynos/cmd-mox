"""Lifetime bounds and bounded worker events for the named-pipe transport.

This module holds the client-lifetime limit, the deadline arithmetic that
enforces it, and the vocabulary used to report worker transitions through the
observability seam. The concurrency and message-size bounds themselves are
transport-neutral and live in :mod:`cmd_mox.ipc._limits`; they are re-exported
here so existing named-pipe imports keep working. This module deliberately
imports no ``pywin32`` module, so it is importable and testable on any
platform.
"""

from __future__ import annotations

import dataclasses as dc
import logging
import time
import typing as typ

from . import _observability
from ._limits import MAX_ACTIVE_CLIENTS, ClientSlot, acquire_client_slot

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import threading

# Wall-clock budget for reading one complete request from a connected client.
# A shim writes its request immediately after connecting, so thirty seconds is
# far more than a healthy peer needs even on a loaded CI machine, while still
# guaranteeing that a client which connects and goes silent cannot pin a worker.
CLIENT_READ_TIMEOUT_SECONDS: typ.Final[float] = 30.0

#: Operation name for every bounded event emitted by a named-pipe worker.
WORKER_OPERATION: typ.Final[str] = "ipc.named_pipe.worker"

# Worker events describe cmd_mox.ipc.named_pipe behaviour, so they are logged
# through that module's logger rather than this helper module's. Naming the
# logger explicitly avoids importing the transport, which would be circular.
_WORKER_LOGGER: typ.Final[logging.Logger] = logging.getLogger("cmd_mox.ipc.named_pipe")

type WorkerOutcome = typ.Literal[
    "admitted", "rejected", "completed", "timeout", "read_size_rejected"
]


@dc.dataclass(frozen=True, slots=True)
class WorkerEvent:
    """Bounded metadata describing one named-pipe worker transition.

    Only closed-set labels and numeric measurements may appear here; payloads,
    pipe names, and exception messages are never recorded. No correlation
    identifier is carried, because a worker never parses the request envelope
    itself. The correlated dispatch record is emitted separately by
    :func:`cmd_mox.ipc._server_core._request_pipeline`.

    Parameters
    ----------
    outcome:
        Terminal state of the worker transition.
    error_category:
        Closed-set failure label, set on rejection, timeout, and error exits.
    duration_ms:
        Measured worker lifetime, set on completion.
    message_size:
        Bytes observed when a read was refused for exceeding the size limit.
    """

    outcome: WorkerOutcome
    error_category: _observability._NamedPipeErrorCategory | None = None
    duration_ms: float | None = None
    message_size: int | None = None


def emit_worker_event(event: WorkerEvent) -> None:
    """Emit *event* through the shared bounded observability seam."""
    _observability.emit(
        _observability.IPCEvent(
            operation=WORKER_OPERATION,
            transport="named_pipe",
            outcome=event.outcome,
            error_category=event.error_category,
            duration_ms=event.duration_ms,
            message_size=event.message_size,
        ),
        logger=_WORKER_LOGGER,
    )


class PipeReadCancelled(Exception):  # ruff: ignore[error-suffix-on-exception-name] - names an internal control-flow signal, not a user-facing error
    """Internal signal that a pending overlapped read was cancelled.

    Attributes
    ----------
    timed_out:
        ``True`` when the per-client read deadline expired, ``False`` when
        server shutdown cancelled the read.
    """

    def __init__(self, *, timed_out: bool) -> None:
        """Record whether the read deadline rather than shutdown cancelled it."""
        super().__init__("named pipe read cancelled")
        self.timed_out = timed_out


def remaining_seconds(deadline: float) -> float | None:
    """Return the time left before *deadline*.

    Parameters
    ----------
    deadline:
        A :func:`time.monotonic` timestamp.

    Returns
    -------
    float | None
        The remaining seconds, or ``None`` once the deadline has passed.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    return remaining


def remaining_ms(deadline: float) -> int:
    """Return the milliseconds left before *deadline*.

    Parameters
    ----------
    deadline:
        A :func:`time.monotonic` timestamp.

    Returns
    -------
    int
        Zero once the deadline has passed, so a wait returns at once.
    """
    remaining = remaining_seconds(deadline)
    if remaining is None:
        return 0
    return max(0, int(remaining * 1000))


def join_threads_before(
    threads: cabc.Iterable[threading.Thread], deadline: float
) -> bool:
    """Join every thread in *threads*, stopping once *deadline* passes.

    Parameters
    ----------
    threads:
        The worker threads to join, in the order they should be awaited.
    deadline:
        A :func:`time.monotonic` timestamp bounding the whole sweep.

    Returns
    -------
    bool
        Whether every thread's join was attempted before the deadline expired.
    """
    for thread in threads:
        remaining = remaining_seconds(deadline)
        if remaining is None:
            return False
        thread.join(remaining)
    return True


__all__ = [
    "CLIENT_READ_TIMEOUT_SECONDS",
    "MAX_ACTIVE_CLIENTS",
    "WORKER_OPERATION",
    "ClientSlot",
    "PipeReadCancelled",
    "WorkerEvent",
    "WorkerOutcome",
    "acquire_client_slot",
    "join_threads_before",
    "remaining_ms",
    "remaining_seconds",
]
