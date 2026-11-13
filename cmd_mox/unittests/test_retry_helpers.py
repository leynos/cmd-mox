"""Unit tests for retry/backoff helpers."""

from __future__ import annotations

from unittest import mock

import pytest

from cmd_mox.ipc.client import (
    RetryConfig,
    RetryStrategy,
    calculate_retry_delay,
    retry_with_backoff,
)


def test_calculate_retry_delay() -> None:
    """Retry delay scales linearly and applies jitter bounds."""
    assert calculate_retry_delay(1, 0.1, 0.0) == pytest.approx(0.2)
    with mock.patch("cmd_mox.ipc.random.uniform", return_value=1.25) as mock_uniform:
        delay = calculate_retry_delay(0, 1.0, 0.5)
        assert delay == pytest.approx(1.25)
        mock_uniform.assert_called_once_with(0.5, 1.5)


def test_retry_with_backoff_retries_then_succeeds() -> None:
    """Helper should retry once and then return the successful value."""
    failures: list[tuple[int, BaseException]] = []
    sleeps: list[float] = []

    def attempt(attempt_idx: int) -> str:
        """Fail the first attempt with OSError, then succeed on later attempts."""
        if attempt_idx == 0:
            raise OSError("temporary")
        return "ok"

    strategy = RetryStrategy(
        on_failure=lambda attempt_idx, exc: failures.append((attempt_idx, exc)),
        sleep=sleeps.append,
    )

    result = retry_with_backoff(
        attempt,
        retry_config=RetryConfig(retries=2, backoff=0.1, jitter=0.0),
        strategy=strategy,
    )

    assert result == "ok"
    assert failures
    assert failures[0][0] == 0
    assert sleeps == [pytest.approx(0.1)]


def test_retry_with_backoff_respects_should_retry() -> None:
    """Non-retryable failures should bubble up immediately."""
    sleeps: list[float] = []
    failures: list[int] = []

    def attempt(_attempt: int) -> str:
        """Raise RuntimeError on every call so the retry policy decides what happens."""
        raise RuntimeError("boom")

    strategy = RetryStrategy(
        on_failure=lambda attempt_idx, _exc: failures.append(attempt_idx),
        should_retry=lambda _exc, _attempt, _max: False,
        sleep=sleeps.append,
    )

    with pytest.raises(RuntimeError):
        retry_with_backoff(
            attempt,
            retry_config=RetryConfig(retries=3, backoff=0.1, jitter=0.0),
            strategy=strategy,
        )

    assert failures == [0]
    assert not sleeps


def test_retry_with_backoff_passes_the_configured_jitter_through() -> None:
    """The retry delay must use the configured jitter, not a substitute."""
    sleeps: list[float] = []

    def attempt(_attempt: int) -> str:
        """Fail every attempt so the backoff delay is observable."""
        raise OSError("temporary")

    strategy = RetryStrategy(sleep=sleeps.append)

    with (
        mock.patch("cmd_mox.ipc.client.random.uniform", return_value=1.5) as uniform,
        pytest.raises(OSError, match="temporary"),
    ):
        retry_with_backoff(
            attempt,
            retry_config=RetryConfig(retries=2, backoff=0.2, jitter=0.5),
            strategy=strategy,
        )

    uniform.assert_called_once_with(0.5, 1.5)
    assert sleeps == [pytest.approx(0.3)], "the jitter was not applied to the delay"


def test_retry_with_backoff_reports_an_exhausted_retrying_loop() -> None:
    """A policy that retries every failure falls through to a RuntimeError."""
    sleeps: list[float] = []

    def attempt(_attempt: int) -> str:
        """Fail every attempt so the policy, not the callable, ends the loop."""
        raise OSError("temporary")

    strategy = RetryStrategy(
        should_retry=lambda _exc, _attempt, _max: True,
        sleep=sleeps.append,
    )
    expected = (
        "Unreachable code reached in retry helper: all attempts exhausted "
        "without returning a value."
    )

    with pytest.raises(RuntimeError) as excinfo:
        retry_with_backoff(
            attempt,
            retry_config=RetryConfig(retries=2, backoff=0.1, jitter=0.0),
            strategy=strategy,
        )

    assert str(excinfo.value) == expected, "the fall-through message changed"
    assert sleeps == [pytest.approx(0.1), pytest.approx(0.2)], (
        "every exhausted attempt must still honour its backoff"
    )
