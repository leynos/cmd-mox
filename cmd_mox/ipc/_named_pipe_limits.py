"""Resource bounds and bounded worker events for the named-pipe transport.

This module holds the concurrency, message-size, and lifetime limits applied by
:mod:`cmd_mox.ipc.named_pipe`, the deadline arithmetic that enforces them, and
the vocabulary used to report them through the observability seam. It
deliberately imports no ``pywin32`` module, so the limits are importable and
testable on any platform.
"""

from __future__ import annotations

import dataclasses as dc
import logging
import threading
import time
import typing as typ

from . import _observability

if typ.TYPE_CHECKING:
    import collections.abc as cabc

# Upper bound on named-pipe clients served concurrently. Each admitted client
# owns an OS thread and a pipe instance, so this caps both. Sixty-four is well
# above the handful of shims a realistic run has in flight, yet small enough
# that a connection flood cannot exhaust the process's thread or handle budget.
MAX_ACTIVE_CLIENTS: typ.Final[int] = 64

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
    error_category: str | None = None
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
        super().__init__("named pipe read cancelled")
        self.timed_out = timed_out


class ClientSlot:
    """One admitted-client permit, returned to the pool at most once.

    The accept loop acquires a permit immediately before spawning a worker and
    hands the resulting slot to that worker, whose ``finally`` block releases
    it. When the thread never starts, the accept loop releases the slot
    instead. Because :meth:`release` latches under a lock, no exit path can
    release twice and none can be missed.
    """

    __slots__ = ("_lock", "_released", "_semaphore")

    def __init__(self, semaphore: threading.BoundedSemaphore) -> None:
        self._semaphore = semaphore
        self._released = False
        self._lock = threading.Lock()

    @property
    def released(self) -> bool:
        """Whether the permit has already been returned.

        Returns
        -------
        bool
            ``True`` once :meth:`release` has run.
        """
        with self._lock:
            return self._released

    def release(self) -> None:
        """Return the permit, ignoring repeated calls."""
        with self._lock:
            if self._released:
                return
            self._released = True
        self._semaphore.release()


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


def acquire_client_slot(semaphore: threading.BoundedSemaphore) -> ClientSlot | None:
    """Reserve one permit from *semaphore* without blocking.

    Returns
    -------
    ClientSlot | None
        The reserved permit, or ``None`` when the limit has been reached.
    """
    if not semaphore.acquire(blocking=False):
        return None
    return ClientSlot(semaphore)


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
