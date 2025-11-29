"""Unit tests for retry/backoff helpers."""

from __future__ import annotations

from unittest.mock import patch

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
    with patch("cmd_mox.ipc.random.uniform", return_value=1.25) as mock_uniform:
        delay = calculate_retry_delay(0, 1.0, 0.5)
        assert delay == pytest.approx(1.25)
        mock_uniform.assert_called_once_with(0.5, 1.5)


def test_retry_with_backoff_retries_then_succeeds() -> None:
    """Helper should retry once and then return the successful value."""
    failures: list[tuple[int, BaseException]] = []
    sleeps: list[float] = []

    def attempt(attempt_idx: int) -> str:
        if attempt_idx == 0:
            raise OSError("temporary")
        return "ok"

    strategy = RetryStrategy(
        log_failure=lambda attempt_idx, exc: failures.append((attempt_idx, exc)),
        sleep=lambda delay: sleeps.append(delay),
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
        raise RuntimeError("boom")

    strategy = RetryStrategy(
        log_failure=lambda attempt_idx, _exc: failures.append(attempt_idx),
        should_retry=lambda _exc, _attempt, _max: False,
        sleep=lambda delay: sleeps.append(delay),
    )

    with pytest.raises(RuntimeError):
        retry_with_backoff(
            attempt,
            retry_config=RetryConfig(retries=3, backoff=0.1, jitter=0.0),
            strategy=strategy,
        )

    assert failures == [0]
    assert sleeps == []
