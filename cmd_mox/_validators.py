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


def validate_optional_timeout(timeout: float | None, *, name: str) -> None:
    """Validate optional timeout values passed to IPC helpers."""
    if timeout is None:
        return

    try:
        validate_positive_finite_timeout(timeout)
    except TypeError as exc:
        msg = f"{name} must be a real number"
        raise TypeError(msg) from exc
    except ValueError as exc:
        msg = f"{name} must be > 0 and finite"
        raise ValueError(msg) from exc


def validate_retry_attempts(retries: int) -> None:
    """Ensure retry attempt counts are sensible."""
    if isinstance(retries, bool):
        msg = "retries must be an integer"
        raise TypeError(msg)

    if retries < 1:
        msg = "retries must be >= 1"
        raise ValueError(msg)


def validate_retry_backoff(backoff: float) -> None:
    """Ensure retry backoff configuration is valid."""
    if isinstance(backoff, bool):
        msg = "backoff must be a real number"
        raise TypeError(msg)

    if not (backoff >= 0 and math.isfinite(backoff)):
        msg = "backoff must be >= 0 and finite"
        raise ValueError(msg)


def validate_retry_jitter(jitter: float) -> None:
    """Ensure retry jitter configuration stays within safe bounds."""
    if isinstance(jitter, bool):
        msg = "jitter must be a real number"
        raise TypeError(msg)

    if not (0.0 <= jitter <= 1.0 and math.isfinite(jitter)):
        msg = "jitter must be between 0 and 1 and finite"
        raise ValueError(msg)
