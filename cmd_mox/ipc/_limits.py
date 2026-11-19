"""Transport-neutral resource bounds shared by the IPC transports.

This module holds the message-size cap, the active-client cap, the permit type
that accounts for admitted clients, and the payload-free size error both
transports raise. It deliberately imports no transport and no ``pywin32``
module, so the bounds are importable and testable on any platform. The
Windows-specific modules re-export these names to keep their existing public
surface stable.
"""

from __future__ import annotations

import dataclasses as dc
import threading
import typing as typ

# Upper bound on clients served concurrently. Each admitted client owns an OS
# thread, so this caps both live workers and the resources each one pins.
# Sixty-four is well above the handful of shims a realistic run has in flight,
# yet small enough that a connection flood cannot exhaust the process's thread
# or handle budget.
MAX_ACTIVE_CLIENTS: typ.Final[int] = 64

# Largest IPC message either transport will buffer for a single client. A real
# invocation payload (command name, argv, stdin, and environment) is kilobytes
# at worst, so 8 MiB leaves several orders of magnitude of headroom while still
# bounding the memory one misbehaving or hostile peer can pin. The limit is
# deliberately conservative: it is cheap to raise, whereas an unbounded reader
# lets a single client exhaust the server process.
MAX_MESSAGE_SIZE: typ.Final[int] = 8 * 1024 * 1024

# Bytes requested per receive by a bounded read. The value is large enough to
# amortise syscalls on a normal payload and small enough that a single receive
# cannot pin excess memory before the size check runs.
DEFAULT_READ_CHUNK_SIZE: typ.Final[int] = 64 * 1024


def validate_read_bounds(*, max_bytes: int, chunk_size: int) -> None:
    """Reject read bounds that cannot describe a usable bounded read.

    Parameters
    ----------
    max_bytes:
        Maximum number of payload bytes accepted.
    chunk_size:
        Upper bound on bytes requested per receive.

    Raises
    ------
    ValueError
        If *max_bytes* or *chunk_size* is not strictly positive.
    """
    if max_bytes <= 0:
        msg = f"max_bytes must be positive, got {max_bytes}"
        raise ValueError(msg)
    if chunk_size <= 0:
        msg = f"chunk_size must be positive, got {chunk_size}"
        raise ValueError(msg)


@dc.dataclass(frozen=True, slots=True)
class ReadBounds:
    """Validated byte budget and receive chunk size for a bounded read.

    Both transports base their read options on this type, so the two
    configuration invariants and their rejection live in one place.

    Parameters
    ----------
    chunk_size:
        Upper bound on bytes requested per receive.
    max_bytes:
        Maximum number of payload bytes accepted.
    """

    # Field order is a compatibility contract, not a preference: dataclass
    # inheritance keeps base fields first, and ``PipeReadOptions`` took
    # ``chunk_size`` before ``max_bytes`` long before this base existed.
    # Reordering these would silently reinterpret every positional call.
    chunk_size: int = DEFAULT_READ_CHUNK_SIZE
    max_bytes: int = MAX_MESSAGE_SIZE

    def __post_init__(self) -> None:
        """Reject bounds that cannot describe a usable read."""
        validate_read_bounds(max_bytes=self.max_bytes, chunk_size=self.chunk_size)


class MessageTooLargeError(RuntimeError):
    """Raised when a message exceeds the transport's configured size limit.

    The exception carries byte counts only. Message content is dropped as soon
    as the limit is breached and is never retained, logged, or embedded in the
    exception, so this error is safe to report through the bounded
    observability seam.

    Attributes
    ----------
    received:
        Number of bytes read when the limit was breached.
    limit:
        Configured maximum message size in bytes.
    """

    # Subclasses keep their established message wording by overriding this
    # prefix; the shared constructor stays the single place that builds args.
    _message_prefix: typ.ClassVar[str] = "message"

    def __init__(self, *, received: int, limit: int) -> None:
        """Store the received byte count and the configured limit."""
        msg = f"{self._message_prefix} exceeded the {limit}-byte limit"
        super().__init__(msg)
        self.received = received
        self.limit = limit


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
        """Bind the slot to the shared semaphore, starting unreleased."""
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
    "DEFAULT_READ_CHUNK_SIZE",
    "MAX_ACTIVE_CLIENTS",
    "MAX_MESSAGE_SIZE",
    "ClientSlot",
    "MessageTooLargeError",
    "ReadBounds",
    "acquire_client_slot",
    "validate_read_bounds",
]
