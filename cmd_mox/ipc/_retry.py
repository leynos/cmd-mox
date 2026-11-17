"""Retry policy and backoff helpers shared by the IPC client transports.

Connection attempts against a Unix socket or a named pipe fail transiently:
the server may still be starting, or every pipe instance may be busy. This
module owns the policy data and the retry loop, while the transports supply
the attempt callable and the logging hook.
"""

from __future__ import annotations

import dataclasses as dc
import random
import time
import typing as typ

from cmd_mox._validators import (
    validate_positive_finite_timeout,
    validate_retry_attempts,
    validate_retry_backoff,
    validate_retry_jitter,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

DEFAULT_CONNECT_RETRIES: typ.Final[int] = 3
DEFAULT_CONNECT_BACKOFF: typ.Final[float] = 0.05
DEFAULT_CONNECT_JITTER: typ.Final[float] = 0.2
MIN_RETRY_SLEEP: typ.Final[float] = 0.001


@dc.dataclass(slots=True)
class RetryConfig:
    """Configuration for connection retry behavior."""

    retries: int = DEFAULT_CONNECT_RETRIES
    backoff: float = DEFAULT_CONNECT_BACKOFF
    jitter: float = DEFAULT_CONNECT_JITTER

    def __post_init__(self) -> None:
        """Validate retry configuration values."""
        validate_retry_attempts(self.retries)
        validate_retry_backoff(self.backoff)
        validate_retry_jitter(self.jitter)

    def validate(self, timeout: float) -> None:
        """Re-validate retry configuration alongside the connection timeout."""
        validate_positive_finite_timeout(timeout)
        self.__post_init__()


@dc.dataclass(slots=True)
class RetryStrategy:
    """Hooks for logging and gating retry behaviour."""

    on_failure: cabc.Callable[[int, Exception], None] | None = None
    should_retry: cabc.Callable[[Exception, int, int], bool] | None = None
    sleep: cabc.Callable[[float], None] = time.sleep


def calculate_retry_delay(attempt: int, backoff: float, jitter: float) -> float:
    """Return the sleep delay for a 0-based *attempt*.

    Never shorter than :data:`MIN_RETRY_SLEEP`.

    Returns
    -------
    float
        The bounded, optionally jittered retry delay in seconds.
    """
    delay = backoff * (attempt + 1)
    if jitter:
        # Randomise the linear backoff within the jitter bounds to avoid
        # thundering herds if many clients retry simultaneously.
        factor = random.uniform(1.0 - jitter, 1.0 + jitter)  # ruff: ignore[suspicious-non-cryptographic-random-usage] - non-cryptographic jitter for retry backoff
        delay *= factor
    return max(delay, MIN_RETRY_SLEEP)


@dc.dataclass(slots=True)
class _RetryContext:
    """Carry the attempt state and retry policy through one retry decision."""

    attempt: int
    max_attempts: int
    retry_config: RetryConfig
    strategy: RetryStrategy


def _handle_retry_failure(
    exc: Exception,
    context: _RetryContext,
) -> float:
    """Process a failure from a retry attempt and return the backoff delay.

    Raises *exc* when no further retries should be attempted.

    Returns
    -------
    float
        The delay before the next retry attempt.
    """
    if context.strategy.on_failure is not None:
        context.strategy.on_failure(context.attempt, exc)

    retry_decider = context.strategy.should_retry or (
        lambda _exc, att, maximum: att < maximum - 1
    )
    if not retry_decider(exc, context.attempt, context.max_attempts):
        raise

    return calculate_retry_delay(
        context.attempt,
        context.retry_config.backoff,
        context.retry_config.jitter,
    )


def retry_with_backoff[T](
    func: cabc.Callable[[int], T],
    *,
    retry_config: RetryConfig,
    strategy: RetryStrategy | None = None,
) -> T:
    """Execute *func* until it succeeds or retries are exhausted.

    The callable receives the 0-based attempt index. When provided, the
    strategy's ``on_failure`` runs on every raised exception (e.g., for
    logging). The strategy's ``should_retry`` decides whether to try again,
    defaulting to retrying until the configured maximum attempts. The delay
    between attempts is calculated from ``retry_config.backoff`` and
    ``retry_config.jitter`` using :func:`calculate_retry_delay`, and the wait
    is performed via ``strategy.sleep`` (defaulting to :func:`time.sleep` when
    *strategy* is ``None``).

    Returns
    -------
    T
        The value returned by the first successful invocation of ``func``.

    Raises
    ------
    RuntimeError
        If the retry loop terminates without returning or propagating a failure.
    """
    max_attempts = retry_config.retries
    strat = strategy if strategy is not None else RetryStrategy()

    for attempt in range(max_attempts):
        try:
            return func(attempt)
        except Exception as exc:  # ruff: ignore[blind-except] - generic retry helper: *func* is caller-supplied and may raise anything
            context = _RetryContext(
                attempt=attempt,
                max_attempts=max_attempts,
                retry_config=retry_config,
                strategy=strat,
            )
            delay = _handle_retry_failure(exc, context)
            strat.sleep(delay)

    msg = (
        "Unreachable code reached in retry helper: all attempts exhausted "
        "without returning a value."
    )
    raise RuntimeError(msg)


__all__ = [
    "DEFAULT_CONNECT_BACKOFF",
    "DEFAULT_CONNECT_JITTER",
    "DEFAULT_CONNECT_RETRIES",
    "MIN_RETRY_SLEEP",
    "RetryConfig",
    "RetryStrategy",
    "calculate_retry_delay",
    "retry_with_backoff",
]
