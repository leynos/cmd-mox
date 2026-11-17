"""Unit tests for the Windows IPC client's pipe-availability wait."""

from __future__ import annotations

import types

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cmd_mox.ipc import client, windows
from tests.helpers.ipc_windows import (
    TIMEOUT_MESSAGE,
    DummyPipeError,
    patch_windows_modules,
)


@pytest.fixture(autouse=True)
def _patch_windows_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub Windows-only modules so the named-pipe client runs on any platform."""
    patch_windows_modules(monkeypatch)


class _PipeWaitClock:
    """Mutable monotonic clock for the pipe-availability tests."""

    def __init__(self, now: float = 0.0) -> None:
        """Start the clock at *now*."""
        self.now = now


def _bind_pipe_wait_clock(
    monkeypatch: pytest.MonkeyPatch, clock: _PipeWaitClock
) -> None:
    """Replace ``remaining`` with one measured against *clock*."""

    def remaining(deadline: float) -> float:
        """Measure the budget against the mutable test clock."""
        left = deadline - clock.now
        if left <= 0:
            raise TimeoutError(TIMEOUT_MESSAGE)
        return left

    monkeypatch.setattr(client, "remaining", remaining)


@pytest.mark.parametrize(
    ("delay", "expected_ms"),
    [
        pytest.param(1.0, 1000, id="whole-second"),
        pytest.param(0.25, 250, id="fractional-second"),
        pytest.param(0.0019, 1, id="sub-millisecond-truncates"),
        pytest.param(0.0004, 1, id="floored-to-one-millisecond"),
    ],
)
def test_wait_for_pipe_availability_bounds_the_wait_in_milliseconds(
    monkeypatch: pytest.MonkeyPatch, delay: float, expected_ms: int
) -> None:
    """Waits convert seconds to milliseconds with a one-millisecond floor."""
    waits: list[tuple[str, int]] = []
    monkeypatch.setattr(
        client,
        "win32pipe",
        types.SimpleNamespace(
            WaitNamedPipe=lambda name, wait_ms: waits.append((name, wait_ms))
        ),
    )

    client._wait_for_pipe_availability("pipe", delay)

    assert waits == [("pipe", expected_ms)], "the wait duration was mis-converted"


@given(
    delay=st.floats(
        min_value=1e-6, max_value=10.0, allow_nan=False, allow_infinity=False
    ),
    remaining=st.floats(
        min_value=1e-6, max_value=10.0, allow_nan=False, allow_infinity=False
    ),
)
def test_wait_for_pipe_availability_bounds_any_wait_by_the_budget(
    delay: float, remaining: float
) -> None:
    """Every positive delay is floored at 1 ms and capped by the budget."""
    waits: list[int] = []

    with pytest.MonkeyPatch.context() as patch:
        # A zero clock makes the remaining budget exactly ``remaining``, so the
        # bounds below describe the helper's arithmetic rather than rounding.
        _bind_pipe_wait_clock(patch, _PipeWaitClock())
        patch.setattr(
            client,
            "win32pipe",
            types.SimpleNamespace(
                WaitNamedPipe=lambda _name, wait_ms: waits.append(wait_ms)
            ),
        )
        client._wait_for_pipe_availability("pipe", delay, deadline=remaining)

    [wait_ms] = waits
    assert wait_ms >= 1, "the wait was not floored at one millisecond"
    assert wait_ms <= max(1, remaining * 1000), (
        "the wait overran the remaining deadline budget"
    )
    assert wait_ms <= max(1, delay * 1000), "the wait overran the requested delay"


def test_wait_for_pipe_availability_caps_the_wait_to_the_remaining_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A near deadline shortens the wait below the requested delay."""
    clock = _PipeWaitClock(now=10.0)
    waits: list[tuple[str, int]] = []
    _bind_pipe_wait_clock(monkeypatch, clock)
    monkeypatch.setattr(
        client,
        "win32pipe",
        types.SimpleNamespace(
            WaitNamedPipe=lambda name, wait_ms: waits.append((name, wait_ms))
        ),
    )

    client._wait_for_pipe_availability("pipe", 1.0, deadline=10.25)

    assert waits == [("pipe", 250)], "the wait overran the remaining deadline"


@pytest.mark.parametrize(
    ("now", "deadline"),
    [
        pytest.param(10.0, 9.75, id="expired"),
        pytest.param(10.25, 10.25, id="exactly-reached"),
    ],
)
def test_wait_for_pipe_availability_refuses_an_exhausted_deadline(
    monkeypatch: pytest.MonkeyPatch, now: float, deadline: float
) -> None:
    """An exhausted deadline raises without polling or sleeping."""
    clock = _PipeWaitClock(now=now)
    waits: list[object] = []
    sleeps: list[float] = []
    _bind_pipe_wait_clock(monkeypatch, clock)
    monkeypatch.setattr(
        client,
        "win32pipe",
        types.SimpleNamespace(WaitNamedPipe=lambda *_args: waits.append(_args)),
    )
    monkeypatch.setattr(client.time, "sleep", sleeps.append)

    with pytest.raises(TimeoutError):
        client._wait_for_pipe_availability("pipe", 1.0, deadline=deadline)

    assert not waits, "an exhausted deadline still polled the pipe"
    assert not sleeps, "an exhausted deadline still slept"


def test_wait_for_pipe_availability_sleeps_for_the_capped_wait_on_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pipe error falls back to sleeping for the bounded wait."""
    clock = _PipeWaitClock(now=10.0)
    sleeps: list[float] = []
    _bind_pipe_wait_clock(monkeypatch, clock)

    def wait(_name: str, _wait_ms: int) -> None:
        """Report the pipe as busy so the fallback sleep runs."""
        raise DummyPipeError(windows.ERROR_PIPE_BUSY)

    monkeypatch.setattr(client, "win32pipe", types.SimpleNamespace(WaitNamedPipe=wait))
    monkeypatch.setattr(client.time, "sleep", sleeps.append)

    client._wait_for_pipe_availability("pipe", 1.0, deadline=10.25)

    assert sleeps == [0.25], "the fallback slept past the remaining deadline"


def test_wait_for_pipe_availability_propagates_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only ``pywintypes.error`` triggers the fallback sleep."""
    error = LookupError("unexpected")
    sleeps: list[float] = []

    def wait(_name: str, _wait_ms: int) -> None:
        """Raise an error type the fallback must not swallow."""
        raise error

    monkeypatch.setattr(client, "win32pipe", types.SimpleNamespace(WaitNamedPipe=wait))
    monkeypatch.setattr(client.time, "sleep", sleeps.append)

    with pytest.raises(LookupError) as excinfo:
        client._wait_for_pipe_availability("pipe", 0.5)

    assert excinfo.value is error, "the unexpected error was replaced"
    assert not sleeps, "an unexpected error still slept"
