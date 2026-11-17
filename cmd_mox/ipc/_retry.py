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
    """Configuration for one bounded retry sequence.

    Attributes
    ----------
    retries:
        Total number of attempts permitted, not the number of retries after
        the first failure: ``retries=3`` allows three calls of the retried
        operation. Must be at least one.
    backoff:
        Base delay between attempts in seconds. Attempt *n* (zero-based)
        waits ``backoff * (n + 1)`` before jitter is applied, and never less
        than :data:`MIN_RETRY_SLEEP`. Must be finite and non-negative.
    jitter:
        Fractional randomisation of each delay, in the inclusive range from
        ``0.0`` (no jitter) to ``1.0`` (up to twice the base delay). Must be
        finite.
    """

    retries: int = DEFAULT_CONNECT_RETRIES
    backoff: float = DEFAULT_CONNECT_BACKOFF
    jitter: float = DEFAULT_CONNECT_JITTER

    def __post_init__(self) -> None:
        """Validate the retry fields as soon as the value is built.

        Raises
        ------
        TypeError
            If a field has the wrong type.
        ValueError
            If a field is outside its documented range.
        """  # ruff: ignore[docstring-extraneous-exception] - the validators raise these caller-visible failures
        validate_retry_attempts(self.retries)
        validate_retry_backoff(self.backoff)
        validate_retry_jitter(self.jitter)

    def validate(self, timeout: float) -> None:
        """Re-validate the configuration alongside a connection timeout.

        Checking the pair together rejects a misconfigured timeout and retry
        policy before the first connection attempt rather than mid-sequence.

        Parameters
        ----------
        timeout:
            Connection timeout in seconds; must be positive and finite.

        Raises
        ------
        TypeError
            If *timeout* or a retry field has the wrong type.
        ValueError
            If *timeout* or a retry field is outside its documented range.
        """  # ruff: ignore[docstring-extraneous-exception] - the validators raise these caller-visible failures
        validate_positive_finite_timeout(timeout)
        self.__post_init__()


@dc.dataclass(slots=True)
class RetryStrategy:
    """Hooks that observe and gate one retry sequence.

    Attributes
    ----------
    on_failure:
        Called with the zero-based index of the attempt that failed and the
        exception it raised, before the retry decision is made. Use it for
        bounded logging; it observes the failure rather than consuming it.
    should_retry:
        Called with the failure, the zero-based attempt index and the total
        attempt count, and returns whether another attempt may run. It can
        narrow which failures are retried but cannot extend the attempt
        limit: the final attempt always re-raises. ``None`` retries every
        failure until that limit.
    sleep:
        Waits for the delay from :func:`calculate_retry_delay`, defaulting to
        :func:`time.sleep`. Tests inject a recorder here.
    """

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

    # The attempt limit is a hard stop. A predicate that only classifies the
    # exception (``isinstance(exc, ConnectionError)``, say) cannot know when
    # the attempts run out, so it must not be allowed to replace the final
    # operation failure with a synthetic loop error.
    if context.attempt >= context.max_attempts - 1:
        raise

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

    Notes
    -----
    When the attempts run out, the last exception ``func`` raised propagates
    unchanged, so callers can still catch the specific error they expect.
    """
    max_attempts = retry_config.retries
    strat = strategy if strategy is not None else RetryStrategy()

    attempt = 0
    while True:
        try:
            return func(attempt)
        except Exception as exc:  # ruff: ignore[blind-except] - generic retry helper: *func* is caller-supplied and may raise anything
            context = _RetryContext(
                attempt=attempt,
                max_attempts=max_attempts,
                retry_config=retry_config,
                strategy=strat,
            )
            strat.sleep(_handle_retry_failure(exc, context))
            # ``_handle_retry_failure`` raises on the final attempt, so the
            # counter never passes ``max_attempts``.
            attempt += 1


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
