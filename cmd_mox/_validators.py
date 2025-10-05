"""Shared validation helpers."""

from __future__ import annotations

import math


def validate_positive_finite_timeout(timeout: float) -> None:
    """Ensure *timeout* represents a usable IPC timeout value."""
    if isinstance(timeout, bool):
        msg = "timeout must be a real number"
        raise TypeError(msg)

    if not (timeout > 0 and math.isfinite(timeout)):
        msg = "timeout must be > 0 and finite"
        raise ValueError(msg)
