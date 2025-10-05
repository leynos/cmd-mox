"""Unit tests for :mod:`cmd_mox._validators`."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox._validators import validate_positive_finite_timeout


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
        validate_positive_finite_timeout(t.cast("float", value))
