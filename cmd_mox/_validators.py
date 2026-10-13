"""Shared validation helpers."""

from __future__ import annotations

import math
import typing as typ

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc


def _validate_numeric(
    value: float,
    *,
    name: str,
    invalid_message: str,
    rejection: cabc.Callable[[float], bool],
) -> None:
    """Reject a bool *value* and values failing *rejection*.

    Every retry and timeout knob is validated the same way: ``bool`` is
    rejected first because it is an ``int`` subclass that would otherwise slip
    through an ``isinstance(value, int)`` check, then the bound is applied.
    Only the bound and the wording differ between callers. The bound is
    applied through *rejection* rather than an ``isinstance`` gate so that any
    real-number type honouring the comparison — ``int``, ``float``,
    ``Decimal`` — is still accepted.

    Parameters
    ----------
    value : float
        Value supplied by the caller.
    name : str
        Parameter name used in the "must be a real number" message.
    invalid_message : str
        Message for a value that is numeric but out of bounds.
    rejection : collections.abc.Callable[[float], bool]
        Reports that *value* is out of bounds.

    Raises
    ------
    TypeError
        If *value* is a bool, or (from the comparison) not a real number.
    ValueError
        If *value* is numeric but *rejection* reports it out of bounds.
    """
    if isinstance(value, bool):
        msg = f"{name} must be a real number"
        raise TypeError(msg)

    if rejection(value):
        raise ValueError(invalid_message)


def validate_positive_finite_timeout(timeout: float) -> None:
    """Ensure *timeout* represents a usable IPC timeout value.

    Parameters
    ----------
    timeout : float
        IPC timeout in seconds.

    Raises
    ------
    TypeError
        If *timeout* is not a real number.
    ValueError
        If *timeout* is not finite and strictly positive.
    """  # ruff: ignore[docstring-extraneous-exception] - _validate_numeric raises these caller-visible failures
    _validate_numeric(
        timeout,
        name="timeout",
        invalid_message="timeout must be > 0 and finite",
        rejection=lambda value: not (value > 0 and math.isfinite(value)),
    )


def validate_optional_timeout(timeout: float | None, *, name: str) -> None:
    """Validate optional timeout values passed to IPC helpers.

    Parameters
    ----------
    timeout : float or None
        Optional IPC timeout in seconds.
    name : str
        Parameter name used in error messages.

    Raises
    ------
    TypeError
        If *timeout* is not ``None`` or a real number.
    ValueError
        If a supplied timeout is not finite and strictly positive.
    """
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
    return


def validate_retry_attempts(retries: int) -> None:
    """Ensure retry attempt counts are sensible.

    Parameters
    ----------
    retries : int
        Number of connection attempts to permit.

    Raises
    ------
    TypeError
        If *retries* is not an integer.
    ValueError
        If *retries* is less than one.
    """
    if isinstance(retries, bool) or not isinstance(retries, int):
        msg = "retries must be an integer"
        raise TypeError(msg)

    if retries < 1:
        msg = "retries must be >= 1"
        raise ValueError(msg)


def validate_retry_backoff(backoff: float) -> None:
    """Ensure retry backoff configuration is valid.

    Parameters
    ----------
    backoff : float
        Initial delay between retries in seconds.

    Raises
    ------
    TypeError
        If *backoff* is not a real number.
    ValueError
        If *backoff* is negative or not finite.
    """  # ruff: ignore[docstring-extraneous-exception] - _validate_numeric raises these caller-visible failures
    _validate_numeric(
        backoff,
        name="backoff",
        invalid_message="backoff must be >= 0 and finite",
        rejection=lambda value: not (value >= 0 and math.isfinite(value)),
    )


def validate_retry_jitter(jitter: float) -> None:
    """Ensure retry jitter configuration stays within safe bounds.

    Parameters
    ----------
    jitter : float
        Randomisation factor applied to the retry backoff delay.

    Raises
    ------
    TypeError
        If *jitter* is not a real number.
    ValueError
        If *jitter* is outside the inclusive range from zero to one or is not
        finite.
    """  # ruff: ignore[docstring-extraneous-exception] - _validate_numeric raises these caller-visible failures
    _validate_numeric(
        jitter,
        name="jitter",
        invalid_message="jitter must be between 0 and 1 and finite",
        rejection=lambda value: not (0.0 <= value <= 1.0 and math.isfinite(value)),
    )
