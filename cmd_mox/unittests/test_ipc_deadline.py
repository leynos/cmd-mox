"""Unit tests for the shared IPC deadline arithmetic."""

from __future__ import annotations

import time

import pytest

from cmd_mox.ipc import _deadline


class _FakeClock:
    """A monotonic clock whose reading the test advances explicitly."""

    def __init__(self, now: float = 0.0) -> None:
        """Start the clock at *now*."""
        self.now = now

    def __call__(self) -> float:
        """Return the current fake reading."""
        return self.now


def test_compute_deadline_offsets_the_injected_clock() -> None:
    """A deadline is the injected reading plus the timeout."""
    clock = _FakeClock(now=5.0)

    deadline = _deadline.compute_deadline(2.5, clock=clock)

    assert deadline == pytest.approx(7.5), "the timeout was not added to the clock"


def test_compute_deadline_defaults_to_the_monotonic_clock() -> None:
    """Without an injected clock the reading comes from ``time.monotonic``."""
    before = time.monotonic()

    deadline = _deadline.compute_deadline(1.0)

    assert before + 1.0 <= deadline <= time.monotonic() + 1.0, (
        "the default clock did not bound the deadline"
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        pytest.param(0.0, 5.0, id="full-budget"),
        pytest.param(4.0, 1.0, id="partly-spent"),
    ],
)
def test_remaining_returns_the_positive_budget(now: float, expected: float) -> None:
    """Every unexpired reading yields the time left."""
    clock = _FakeClock(now)

    assert _deadline.remaining(5.0, clock=clock) == pytest.approx(expected), (
        "the remaining budget was misreported"
    )


@pytest.mark.parametrize("now", [5.0, 6.0], ids=["exact-expiry", "past-expiry"])
def test_remaining_raises_at_and_after_expiry(now: float) -> None:
    """An exact-zero remainder already counts as expired."""
    clock = _FakeClock(now)

    with pytest.raises(TimeoutError) as excinfo:
        _deadline.remaining(5.0, clock=clock)

    assert str(excinfo.value) == _deadline.TIMEOUT_MESSAGE, (
        "the fixed timeout diagnostic changed"
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        pytest.param(0.0, 5.0, id="positive"),
        pytest.param(5.0, None, id="exact-expiry"),
        pytest.param(6.0, None, id="past-expiry"),
    ],
)
def test_remaining_or_none_reports_absence_without_raising(
    now: float, expected: float | None
) -> None:
    """Expiry is reported as ``None`` rather than an exception."""
    clock = _FakeClock(now)

    assert _deadline.remaining_or_none(5.0, clock=clock) == expected, (
        "expiry was not reported as absence"
    )


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        pytest.param(0.0, 5000, id="whole-seconds"),
        pytest.param(4.9995, 0, id="sub-millisecond-floor"),
        pytest.param(4.0, 1000, id="whole-millisecond"),
        pytest.param(3.25, 1750, id="fractional-millisecond-truncated"),
        pytest.param(5.0, 0, id="exact-expiry"),
        pytest.param(6.0, 0, id="past-expiry"),
    ],
)
def test_remaining_ms_floors_at_zero(now: float, expected: int) -> None:
    """Non-positive and sub-millisecond remainders never go negative."""
    clock = _FakeClock(now)

    assert _deadline.remaining_ms(5.0, clock=clock) == expected, (
        "the millisecond conversion or clamp changed"
    )


@pytest.mark.parametrize(
    ("timeout", "expected"),
    [
        pytest.param(0.25, 250, id="quarter-second"),
        pytest.param(2.0, 2000, id="two-seconds"),
        pytest.param(0.0004, 1, id="sub-millisecond-clamped"),
        pytest.param(0.0, 1, id="zero-clamped"),
        pytest.param(-1.0, 1, id="negative-clamped"),
    ],
)
def test_timeout_ms_clamps_to_at_least_one(timeout: float, expected: int) -> None:
    """A relative timeout always becomes a wait of at least one millisecond."""
    assert _deadline.timeout_ms(timeout) == expected, "the clamp or conversion changed"
