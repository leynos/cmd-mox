"""Unit tests for :mod:`cmd_mox._validators`."""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox._validators import (
    validate_expectation_count,
    validate_positive_finite_timeout,
)


@pytest.mark.parametrize("value", [0.1, 1, 5.5])
def test_validate_positive_finite_timeout_accepts_positive_finite(value: float) -> None:
    """Positive finite values should pass validation."""
    validate_positive_finite_timeout(value)


@pytest.mark.parametrize("value", [0, -1, -0.5, float("nan"), float("inf")])
def test_validate_positive_finite_timeout_rejects_invalid_values(value: float) -> None:
    """Non-positive or non-finite values should raise ValueError."""
    msg = "timeout must be > 0 and finite"
    with pytest.raises(ValueError, match=msg):
        validate_positive_finite_timeout(value)


@pytest.mark.parametrize(
    "value",
    [None, True, [], {}, "5", complex(1, 2)],
)
def test_validate_positive_finite_timeout_rejects_invalid_types(value: object) -> None:
    """Non-real inputs should raise TypeError before numeric checks."""
    with pytest.raises(TypeError):
        validate_positive_finite_timeout(typ.cast("float", value))


@pytest.mark.parametrize(
    "count",
    [
        pytest.param(0, id="zero"),
        pytest.param(1, id="one"),
        pytest.param(3, id="three"),
    ],
)
def test_validate_expectation_count_accepts_non_negative_integers(
    count: int,
) -> None:
    """Zero and positive integer counts are valid and return None."""
    assert validate_expectation_count(count) is None


@pytest.mark.parametrize(
    "count",
    [
        pytest.param(True, id="true"),
        pytest.param(False, id="false"),
        pytest.param(2.5, id="float"),
        pytest.param("2", id="string"),
        pytest.param(None, id="none"),
    ],
)
def test_validate_expectation_count_rejects_non_integers(count: object) -> None:
    """Bools and non-integers raise TypeError before the bounds check."""
    with pytest.raises(TypeError, match=r"^count must be an integer$"):
        validate_expectation_count(typ.cast("int", count))


@pytest.mark.parametrize(
    "count",
    [
        pytest.param(-1, id="minus-one"),
        pytest.param(-5, id="minus-five"),
    ],
)
def test_validate_expectation_count_rejects_negative(count: int) -> None:
    """Negative counts raise ValueError."""
    with pytest.raises(ValueError, match=r"^count must be >= 0$"):
        validate_expectation_count(count)
