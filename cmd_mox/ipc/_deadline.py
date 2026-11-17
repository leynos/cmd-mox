"""Keep one monotonic timeout budget across IPC operations.

Compute a deadline once for a request and pass it through connection retries,
server receives, and response reads. Inject the monotonic clock so tests can
control deadline progression without replacing process-wide time functions.
Ask :func:`remaining` before each blocking operation so the timeout applied to
each attempt is limited by the original budget.

This module owns every deadline contract in :mod:`cmd_mox.ipc`:

* :func:`compute_deadline` turns a relative timeout into an absolute
  :func:`time.monotonic` reading;
* :func:`remaining` returns the strictly positive budget left, raising
  :class:`TimeoutError` once it is spent;
* :func:`remaining_or_none` returns the same budget, or ``None`` once spent;
* :func:`remaining_ms` converts the budget to whole milliseconds for Win32
  waits, flooring once spent;
* :func:`timeout_ms` converts a configured relative timeout to whole
  milliseconds, clamping to at least one.

A remainder of exactly ``0.0`` counts as expired. Callers that treat an absent
deadline as "unbounded" must test ``is None`` rather than truthiness, because a
computed deadline is an absolute clock reading that may legitimately be ``0``.

Examples
--------
``clock = time.monotonic``
``deadline = compute_deadline(timeout, clock=clock)``
``sock.settimeout(remaining(deadline, clock=clock))``
``win32event.WaitForMultipleObjects(handles, False, remaining_ms(deadline))``
"""

from __future__ import annotations

import collections.abc as cabc
import time
import typing as typ

type _MonotonicClock = cabc.Callable[[], float]

#: Fixed diagnostic raised by every deadline expiry. Callers that need a
#: transport-specific message translate this at their own boundary.
TIMEOUT_MESSAGE: typ.Final[str] = "IPC client operation timed out"


def _monotonic(clock: _MonotonicClock | None) -> _MonotonicClock:
    """Return the selected monotonic clock, defaulting to :func:`time.monotonic`."""
    return time.monotonic if clock is None else clock


def compute_deadline(
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
    return _monotonic(clock)() + timeout


def remaining(
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
        The deadline has already passed, including when the remainder is
        exactly ``0.0``.
    """
    left = remaining_or_none(deadline, clock=clock)
    if left is None:
        raise TimeoutError(TIMEOUT_MESSAGE)
    return left


def remaining_or_none(
    deadline: float,
    *,
    clock: _MonotonicClock | None = None,
) -> float | None:
    """Return the strictly positive time left before *deadline*, or ``None``.

    Parameters
    ----------
    deadline:
        An absolute reading on the selected monotonic clock.
    clock:
        Clock used to read the current time, defaulting to
        :func:`time.monotonic`.

    Returns
    -------
    float | None
        The remaining seconds, or ``None`` once the remainder is zero or
        negative.
    """
    left = deadline - _monotonic(clock)()
    return left if left > 0 else None


def remaining_ms(
    deadline: float,
    *,
    clock: _MonotonicClock | None = None,
) -> int:
    """Return the whole milliseconds left before *deadline*.

    A remainder below one millisecond converts to ``0`` and a spent deadline
    also yields ``0``, so a Win32 wait is asked to return at once rather than
    receiving a negative timeout.

    Returns
    -------
    int
        Non-negative whole milliseconds remaining.
    """
    left = remaining_or_none(deadline, clock=clock)
    return 0 if left is None else max(0, int(left * 1000))


def timeout_ms(timeout: float) -> int:
    """Convert a relative *timeout* in seconds to whole milliseconds.

    The result is clamped to at least one millisecond: Win32 treats ``0`` as
    "return immediately", which would defeat a sub-millisecond wait rather than
    bounding it.

    Parameters
    ----------
    timeout:
        A positive, finite timeout in seconds.

    Returns
    -------
    int
        The timeout in milliseconds, always at least ``1``.
    """
    return max(1, int(timeout * 1000))


__all__ = [
    "TIMEOUT_MESSAGE",
    "compute_deadline",
    "remaining",
    "remaining_ms",
    "remaining_or_none",
    "timeout_ms",
]
