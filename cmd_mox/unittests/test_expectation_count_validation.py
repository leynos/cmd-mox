"""Unit tests for the fluent expectation count setters."""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox import CmdMox
from cmd_mox.expectations import Expectation

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc

    from cmd_mox.test_doubles import CommandDouble


class _CountSetter(typ.Protocol):
    """Fluent count setters shared by expectations and command doubles."""

    def times(self, count: int) -> typ.Self:
        """Require the command to be invoked exactly *count* times."""

    def times_called(self, count: int) -> typ.Self:
        """Require the command to be invoked exactly *count* times."""


class _InvalidCount(typ.NamedTuple):
    """An invalid count and the failure it should produce."""

    value: object
    exception: type[Exception]
    message: str


def _expectation_for(receiver: _CountSetter) -> Expectation:
    """Return the expectation backing *receiver*."""
    if isinstance(receiver, Expectation):
        return receiver
    return typ.cast("CommandDouble", receiver).expectation


_RECEIVER_PARAMS = pytest.mark.parametrize(
    "make_receiver",
    [
        pytest.param(lambda: Expectation("cmd"), id="expectation"),
        pytest.param(lambda: CmdMox().mock("cmd"), id="command-double"),
    ],
)

_SET_COUNT_PARAMS = pytest.mark.parametrize(
    "set_count",
    [
        pytest.param(lambda receiver, count: receiver.times(count), id="times"),
        pytest.param(
            lambda receiver, count: receiver.times_called(count),
            id="times_called",
        ),
    ],
)


@_RECEIVER_PARAMS
@_SET_COUNT_PARAMS
@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _InvalidCount(-1, ValueError, r"^count must be >= 0$"), id="negative"
        ),
        pytest.param(
            _InvalidCount(
                value=True,
                exception=TypeError,
                message=r"^count must be an integer$",
            ),
            id="bool",
        ),
        pytest.param(
            _InvalidCount(2.5, TypeError, r"^count must be an integer$"), id="float"
        ),
        pytest.param(
            _InvalidCount("2", TypeError, r"^count must be an integer$"), id="string"
        ),
    ],
)
def test_invalid_count_is_rejected_without_mutation(
    make_receiver: cabc.Callable[[], _CountSetter],
    set_count: cabc.Callable[[_CountSetter, int], object],
    case: _InvalidCount,
) -> None:
    """An invalid count raises and leaves the configured count untouched."""
    receiver = make_receiver()
    set_count(receiver, 3)

    with pytest.raises(case.exception, match=case.message):
        set_count(receiver, typ.cast("int", case.value))

    assert _expectation_for(receiver).count == 3


@_RECEIVER_PARAMS
@_SET_COUNT_PARAMS
@pytest.mark.parametrize(
    "count",
    [
        pytest.param(0, id="zero"),
        pytest.param(2, id="two"),
    ],
)
def test_valid_count_is_stored_and_returns_the_receiver(
    make_receiver: cabc.Callable[[], _CountSetter],
    set_count: cabc.Callable[[_CountSetter, int], object],
    count: int,
) -> None:
    """A valid count is stored and the fluent receiver is returned."""
    receiver = make_receiver()

    returned = set_count(receiver, count)

    assert _expectation_for(receiver).count == count
    assert returned is receiver
