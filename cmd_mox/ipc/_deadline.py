"""Keep one monotonic timeout budget across IPC client operations.

Compute a deadline once for a request and pass it through connection retries
and response reads. Ask ``_remaining_time`` before each blocking operation so
the timeout applied to each attempt is limited by the original budget.

Examples
--------
``deadline = _compute_deadline(timeout)``
``sock.settimeout(_remaining_time(deadline))``
"""

from __future__ import annotations

import time


def _compute_deadline(timeout: float) -> float:
    """Return the monotonic deadline *timeout* seconds from now."""  # ruff: ignore[docstring-missing-returns] - private helper has one direct result
    return time.monotonic() + timeout


def _remaining_time(deadline: float) -> float:
    """Return the strictly positive time remaining before *deadline*."""  # ruff: ignore[docstring-missing-returns, docstring-missing-exception] - private helper has one direct result and one direct timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        msg = "IPC client operation timed out"
        raise TimeoutError(msg)
    return remaining
