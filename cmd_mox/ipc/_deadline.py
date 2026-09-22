"""Deadline helpers shared by IPC client transports."""

from __future__ import annotations

import time


def _compute_deadline(timeout: float) -> float:
    """Return the monotonic deadline *timeout* seconds from now.

    Returns
    -------
    float
        The monotonic clock value at which the timeout expires.
    """
    return time.monotonic() + timeout


def _remaining_time(deadline: float) -> float:
    """Return the strictly positive time remaining before *deadline*.

    Returns
    -------
    float
        Seconds remaining before the deadline.

    Raises
    ------
    TimeoutError
        If the deadline has already passed.
    """
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        msg = "IPC client operation timed out"
        raise TimeoutError(msg)
    return remaining
