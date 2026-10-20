"""Tests for shim IPC dispatch deadlines."""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox import shim
from cmd_mox.ipc import Invocation, _observability
from cmd_mox.shim import _execute_invocation
from cmd_mox.unittests._shim_test_support import _assert_exit_code


def test_invocation_deadline_expiry_emits_bounded_event(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Expiry before initial dispatch records one bounded failure."""
    invocation = Invocation(
        command="private-command", args=[], stdin="", env={}, invocation_id="abc"
    )
    monkeypatch.setattr(shim.time, "monotonic", lambda: 101.0)
    ticks = iter((200.0, 200.25))
    monkeypatch.setattr(shim.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(shim.path_utils, "IS_WINDOWS", False)

    def unexpected_request(*_args: object, **_kwargs: object) -> typ.NoReturn:
        """Reject dispatch after the shared deadline expires.

        Returns
        -------
        NoReturn
            Never returns because dispatch after expiry is a test failure.
        """
        return pytest.fail("expired invocation must not be dispatched")

    monkeypatch.setattr(shim, "invoke_server", unexpected_request)
    counters_before = _observability.counter_snapshot()
    with (
        _observability.capture_events() as events,
        pytest.raises(SystemExit) as exc,
    ):
        _execute_invocation(invocation, timeout=1.0, deadline=101.0)

    _assert_exit_code(exc, 1)
    assert capsys.readouterr().err == "IPC error: IPC operation timed out\n", (
        "Expiry changed the controlled IPC diagnostic"
    )
    [event] = events
    assert event.operation == "ipc.shim.ipc_request", "Wrong expiry operation"
    assert event.outcome == "deadline_expired", "Wrong expiry outcome"
    assert event.error_category == "TimeoutError", "Wrong expiry error category"
    assert event.transport == "unix", "Expiry lost its transport"
    assert event.correlation_id == "abc", "Expiry lost its invocation id"
    assert event.duration_ms == pytest.approx(250.0), (
        "Expiry duration must use the performance clock"
    )
    counts_after = _observability.counter_snapshot()
    assert (
        counts_after.get(event.key(), 0) == counters_before.get(event.key(), 0) + 1
    ), "Expiry must increment exactly one bounded event counter"
