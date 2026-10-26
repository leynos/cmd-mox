"""Keep one monotonic timeout budget across IPC operations.

Compute a deadline once for a request and pass it through connection retries,
server receives, and response reads. Inject the monotonic clock so tests can
control deadline progression without replacing process-wide time functions.
Ask ``_remaining_time`` before each blocking operation so the timeout applied
to each attempt is limited by the original budget.

Examples
--------
``clock = time.monotonic``
``deadline = _compute_deadline(timeout, clock=clock)``
``sock.settimeout(_remaining_time(deadline, clock=clock))``
"""

from __future__ import annotations

import collections.abc as cabc
import time

type _MonotonicClock = cabc.Callable[[], float]


def _compute_deadline(
    timeout: float,
    *,
    clock: _MonotonicClock | None = None,
) -> float:
    """Return the monotonic deadline *timeout* seconds from now.

    A supplied clock makes deadline progression deterministic in tests.
    Runtime callers can omit it to use ``time.monotonic``.

    Returns
    -------
    float
        The deadline on the selected monotonic clock's time scale.
    """
    monotonic = time.monotonic if clock is None else clock
    return monotonic() + timeout


def _remaining_time(
    deadline: float,
    *,
    clock: _MonotonicClock | None = None,
) -> float:
    """Return the strictly positive time remaining before *deadline*.

    A supplied clock makes deadline progression deterministic in tests.
    Runtime callers can omit it to use ``time.monotonic``.

    Returns
    -------
    float
        The positive time left before the deadline.

    Raises
    ------
    TimeoutError
        The deadline has already passed.
    """
    monotonic = time.monotonic if clock is None else clock
    remaining = deadline - monotonic()
    if remaining <= 0:
        msg = "IPC client operation timed out"
        raise TimeoutError(msg)
    return remaining
