"""Unit tests for the shared bounded-worker cancellation helpers."""

from __future__ import annotations

import threading
import typing as typ

import pytest

from cmd_mox import _bounded_thread

if typ.TYPE_CHECKING:
    import collections.abc as cabc


class _StalledWorker:
    """A real daemon thread whose body the test releases explicitly."""

    def __init__(self) -> None:
        self.release = threading.Event()
        self.exited = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            self.release.wait(5.0)
        finally:
            self.exited.set()

    def finish(self) -> None:
        """Release the worker and wait for it to unwind."""
        self.release.set()
        self.thread.join(5.0)


@pytest.fixture
def stalled() -> cabc.Iterator[_StalledWorker]:
    """Provide a releasable worker that is always joined on teardown.

    Yields
    ------
    _StalledWorker
        A started worker still waiting inside its body.
    """
    worker = _StalledWorker()
    try:
        yield worker
    finally:
        worker.finish()


class _SteppingTestClock:
    """Mutable clock advanced explicitly by the doubles in this module."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        """Return the current fake time.

        Returns
        -------
        float
            The time last assigned to :attr:`now`.
        """
        return self.now


class _RecordingEvent:
    """Event double that records each wait and rejects out-of-range slices.

    A real :meth:`threading.Event.wait` raises :exc:`OverflowError` for a
    timeout beyond :data:`threading.TIMEOUT_MAX`. Sleeping for such a slice
    would outlive the suite, so this double reproduces the rejection and
    advances a shared clock by each accepted slice instead.
    """

    def __init__(self, clock: _SteppingTestClock) -> None:
        self.clock = clock
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        """Record *timeout*, rejecting values CPython cannot schedule.

        Returns
        -------
        bool
            ``False``, because the simulated worker never finishes.

        Raises
        ------
        OverflowError
            If *timeout* exceeds :data:`threading.TIMEOUT_MAX`.
        """
        if timeout is not None and timeout > threading.TIMEOUT_MAX:
            msg = "timestamp out of range for platform time_t"
            raise OverflowError(msg)
        assert timeout is not None, "the helper always waits with a timeout"
        self.waits.append(timeout)
        self.clock.now += timeout
        return False


class _CompletingEvent:
    """Event double that completes and reports the fake time it happened at.

    ``Event.wait`` reports only that the event is set, not when it was set. The
    double models a worker that finished at *completes_at*, which the waiting
    thread observes when it resumes; advancing the clock to that instant is
    what makes the late-completion boundary deterministic.
    """

    def __init__(self, clock: _SteppingTestClock, completes_at: float) -> None:
        self.clock = clock
        self.completes_at = completes_at

    def wait(self, timeout: float | None = None) -> bool:
        """Advance to the completion instant and report the event as set.

        Returns
        -------
        bool
            Always ``True``: completion is observable, but not necessarily in
            time.
        """
        assert timeout is not None, "the helper always waits with a timeout"
        self.clock.now = max(self.clock.now, self.completes_at)
        return True


class _OversleepingEvent:
    """Event double whose waiter resumes half a second after it asked to.

    Models a descheduled waiting thread: the real elapsed time outruns the
    slice the helper requested, so only a clock-based budget notices.
    """

    def __init__(self, clock: _SteppingTestClock) -> None:
        self.clock = clock
        self.waits: list[float] = []

    def wait(self, timeout: float | None = None) -> bool:
        """Record *timeout* and advance the clock by half a second.

        Returns
        -------
        bool
            ``False``, because the simulated worker never finishes.
        """
        assert timeout is not None, "the helper always waits with a timeout"
        self.waits.append(timeout)
        self.clock.now += 0.5
        return False


def test_wait_for_finish_returns_true_when_the_event_is_set() -> None:
    """A worker that has already finished is reported as finished."""
    event = threading.Event()
    event.set()
    clock = _SteppingTestClock()

    assert _bounded_thread.wait_for_finish(event, 1.0, clock=clock) is True, (
        "a set event should report completion"
    )


def test_wait_for_finish_returns_false_at_an_expired_deadline() -> None:
    """An already-expired deadline reports failure without waiting."""
    clock = _SteppingTestClock()
    clock.now = 10.0
    event = _RecordingEvent(clock)

    assert _bounded_thread.wait_for_finish(event, 5.0, clock=clock) is False, (
        "an expired deadline should report failure"
    )
    assert event.waits == [], "an expired deadline must not wait"


@pytest.mark.parametrize(
    ("completes_at", "expected"),
    [
        (0.5, True),
        (1.0, False),
        (2.0, False),
    ],
    ids=["before", "at", "after"],
)
def test_wait_for_finish_only_accepts_completion_before_the_deadline(
    completes_at: float, *, expected: bool
) -> None:
    """Late worker completion is a timeout, not a result.

    ``Event.wait`` cannot report when the event was set, so a waiter that is
    descheduled while its worker finishes could otherwise accept a result
    produced after the deadline. Completion exactly at the deadline counts as
    late, matching the polled reader.
    """
    clock = _SteppingTestClock()
    event = _CompletingEvent(clock, completes_at)

    assert _bounded_thread.wait_for_finish(event, 1.0, clock=clock) is expected, (
        f"completion at {completes_at} was not classified correctly"
    )


def test_wait_for_finish_recomputes_the_budget_from_the_clock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A waiter that oversleeps stops once the clock passes the deadline.

    The helper must bound each slice by the clock's remaining time, not only
    by the slice it asked to wait for: a descheduled thread can consume far
    more real time than it requested, and continuing would overshoot the
    deadline it promised to enforce.
    """
    clock = _SteppingTestClock()
    event = _OversleepingEvent(clock)
    # Force several slices so the clock and the accumulated bookkeeping can
    # diverge, which they cannot when one slice covers the whole budget.
    monkeypatch.setattr(_bounded_thread, "_MAX_EVENT_WAIT", 0.1)

    assert _bounded_thread.wait_for_finish(event, 1.0, clock=clock) is False, (
        "a worker that never finishes must report failure"
    )
    assert len(event.waits) == 2, (
        f"waits were not bounded by the clock; observed {event.waits}"
    )


def test_wait_for_finish_slices_waits_beyond_the_platform_maximum() -> None:
    """A deadline beyond ``TIMEOUT_MAX`` is waited for in legal slices.

    ``CMOX_IPC_TIMEOUT`` accepts any finite positive float, so a caller can
    hand this helper a deadline :meth:`threading.Event.wait` cannot schedule.
    Without slicing the helper would raise :exc:`OverflowError`, which no shim
    stdin handler catches, instead of reporting the deadline as expired.
    """
    clock = _SteppingTestClock()
    event = _RecordingEvent(clock)

    finished = _bounded_thread.wait_for_finish(event, 1e10, clock=clock)

    assert finished is False, "the simulated worker never finishes"
    assert len(event.waits) > 1, "a deadline beyond the maximum was not sliced"
    assert all(wait <= threading.TIMEOUT_MAX for wait in event.waits), (
        "a wait beyond the platform limit reached Event.wait"
    )
    assert sum(event.waits) == pytest.approx(1e10), (
        "the slices did not cover the whole deadline"
    )


def test_cancel_is_reissued_while_the_worker_survives(
    stalled: _StalledWorker,
) -> None:
    """Cancellation is retried until the worker exits or the grace expires.

    ``CancelSynchronousIo`` fails with ``ERROR_NOT_FOUND`` when its target sits
    between two ``ReadFile`` calls. The reader loop decodes after every chunk,
    so that window is reachable; spending the only cancellation attempt on it
    would leave the worker alive and push the shim onto its forced-exit path.
    """
    attempts: list[threading.Thread] = []

    def cancel() -> None:
        """Release the worker so the re-issued cancel can observe its exit."""

    def request_cancel(thread: threading.Thread) -> None:
        attempts.append(thread)
        # Emulate the first call landing in the gap between reads, and the
        # worker then reaching its blocking call and being cancelled.
        if len(attempts) == 2:
            stalled.release.set()

    exited = _bounded_thread.cancel_and_await_worker(
        stalled.thread,
        cancel,
        grace=5.0,
        request_cancel=request_cancel,
    )

    assert exited is True, "the worker did not exit within the grace period"
    assert len(attempts) >= 2, (
        f"cancellation was not re-issued; attempts: {len(attempts)}"
    )
    assert all(attempt is stalled.thread for attempt in attempts), (
        "cancellation targeted the wrong thread"
    )


def test_cancel_returns_false_when_the_worker_survives_the_grace(
    stalled: _StalledWorker,
) -> None:
    """A worker that resists cancellation is reported as still alive."""

    def request_cancel(_thread: threading.Thread) -> None:
        """Never reach the worker, so it keeps waiting."""

    exited = _bounded_thread.cancel_and_await_worker(
        stalled.thread,
        lambda: None,
        grace=0.05,
        request_cancel=request_cancel,
    )

    assert exited is False, "a surviving worker must be reported as alive"


def test_cancel_applies_the_fallback_cancel_once(
    stalled: _StalledWorker,
) -> None:
    """The resource-level fallback runs exactly once, after the first request."""
    calls: list[str] = []

    def cancel() -> None:
        calls.append("cancel")
        stalled.release.set()

    def request_cancel(_thread: threading.Thread) -> None:
        calls.append("request")

    exited = _bounded_thread.cancel_and_await_worker(
        stalled.thread,
        cancel,
        grace=5.0,
        request_cancel=request_cancel,
    )

    assert exited is True, "the worker did not exit within the grace period"
    assert calls.count("cancel") == 1, "the fallback cancel ran more than once"
    assert calls[0] == "request", "the native cancel must be attempted first"
