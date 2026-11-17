"""Unit tests for the IPC client's bounded blocking-I/O helpers.

These tests run on every platform: the helpers under test spawn ordinary
threads and never touch pywin32. They complement the Windows-facing pipe tests
by pinning the worker lifecycle, deadline handling, and timeout reporting.
"""

from __future__ import annotations

import threading
import time
import types
import typing as typ

import pytest

from cmd_mox.ipc import client

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_SAFETY_TIMEOUT = 5.0


class _ThreadRecorder:
    """Thread factory that records constructor arguments and builds real threads."""

    def __init__(self) -> None:
        """Start with no recorded constructions."""
        self.calls: list[dict[str, object]] = []
        self.created: list[threading.Thread] = []

    def __call__(
        self,
        *,
        target: cabc.Callable[[], object],
        name: str | None = None,
        daemon: bool | None = None,
    ) -> threading.Thread:
        """Record the constructor arguments, then build the real thread."""
        self.calls.append({"target": target, "name": name, "daemon": daemon})
        thread = threading.Thread(target=target, name=name, daemon=daemon)
        self.created.append(thread)
        return thread

    def bind(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Serve as the client module's ``threading.Thread`` for one test."""
        monkeypatch.setattr(client, "threading", types.SimpleNamespace(Thread=self))


def _event_blocked_worker() -> tuple[threading.Thread, threading.Event]:
    """Start a daemon worker that stays alive until its release event is set.

    This is real-thread coverage of the cancellation lifecycle. The wait is
    bounded so the worker terminates even when a mutant never cancels it:
    the suite then fails on its assertions rather than hanging.

    Returns
    -------
    tuple[threading.Thread, threading.Event]
        The started daemon thread and the event that ends it.
    """
    release = threading.Event()
    started = threading.Event()

    def block() -> None:
        """Stay alive until the test releases the worker."""
        started.set()
        release.wait(_SAFETY_TIMEOUT)

    worker = threading.Thread(target=block, daemon=True)
    worker.start()
    assert started.wait(_SAFETY_TIMEOUT), "the worker never started"
    return worker, release


def _join_bounded(worker: threading.Thread) -> None:
    """Release-safe join used by test clean-up."""
    worker.join(_SAFETY_TIMEOUT)
    assert not worker.is_alive(), "the worker outlived the test"


class _ControlledWorker:
    """Thread stand-in whose liveness the test releases explicitly.

    The timeout branch must be observed without depending on how promptly a
    real thread is scheduled. A release flag makes the worker's state
    deterministic, and because ``join`` never blocks, a mutant that drops the
    join timeout still fails the assertions instead of hanging the suite.
    """

    def __init__(self, *, exits_on_cancel: bool) -> None:
        """Choose whether cancelling the worker ends it."""
        self.exits_on_cancel = exits_on_cancel
        self.released = False
        self.joins: list[float | None] = []

    def start(self) -> None:
        """Run no target; the test drives the worker's state instead."""

    def join(self, timeout: float | None = None) -> bool:
        """Record the budget and report whether the worker had exited."""
        self.joins.append(timeout)
        return not self.is_alive()

    def is_alive(self) -> bool:
        """Report liveness until the worker is released, if it ever is."""
        return not (self.exits_on_cancel and self.released)


def test_run_blocking_io_uses_a_named_daemon_worker_and_returns_its_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The worker is a daemon named for diagnosis and its value is returned."""
    recorder = _ThreadRecorder()
    recorder.bind(monkeypatch)

    result = client._run_blocking_io(
        lambda: "value",
        deadline=time.monotonic() + _SAFETY_TIMEOUT,
        cancel=lambda: None,
    )

    assert result == "value", "the worker's return value was not forwarded"
    [construction] = recorder.calls
    assert construction["name"] == "cmd-mox-ipc-io", "the worker name changed"
    assert construction["daemon"] is True, "the I/O worker must be a daemon"
    assert callable(construction["target"]), "the worker has no target"


def test_run_blocking_io_reraises_the_worker_exception() -> None:
    """An exception raised on the worker surfaces on the caller's thread."""
    failure = RuntimeError("worker failed")

    def fail() -> None:
        """Raise the sentinel failure."""
        raise failure

    with pytest.raises(RuntimeError) as excinfo:
        client._run_blocking_io(
            fail, deadline=time.monotonic() + _SAFETY_TIMEOUT, cancel=lambda: None
        )

    assert excinfo.value is failure, "the worker's exception identity was lost"


def test_run_blocking_io_rejects_a_spent_deadline_before_starting_a_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired deadline fails before any worker exists to cancel."""
    starts: list[str] = []
    cancels: list[str] = []

    def spent(_deadline: float) -> float:
        """Report an exhausted deadline the way ``remaining`` does."""
        raise TimeoutError

    def thread(**_kwargs: object) -> threading.Thread:
        """Fail the test if a worker is constructed."""
        starts.append("started")
        msg = "a spent deadline must not start a worker"
        raise AssertionError(msg)

    monkeypatch.setattr(client, "remaining", spent)
    monkeypatch.setattr(client, "threading", types.SimpleNamespace(Thread=thread))

    with pytest.raises(TimeoutError):
        client._run_blocking_io(
            lambda: "too late",
            deadline=0.0,
            cancel=lambda: cancels.append("called"),
        )

    assert not starts, "an expired deadline still started the I/O worker"
    assert not cancels, "an unstarted worker must not be cancelled"


def test_run_blocking_io_cancels_a_worker_that_outlives_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unfinished worker is cancelled once its budget is spent.

    A stand-in clock and a stand-in worker select the branch, so thread startup
    on a loaded host cannot turn the live deadline into an expired one.
    """
    monkeypatch.setattr(client, "remaining", lambda _deadline: 0.05)
    monkeypatch.setattr(client, "IO_CANCEL_GRACE", 0.01)
    worker = _ControlledWorker(exits_on_cancel=True)
    cancels: list[str] = []

    def cancel() -> None:
        """Record the cancellation and end the worker."""
        cancels.append("called")
        worker.released = True

    monkeypatch.setattr(
        client, "threading", types.SimpleNamespace(Thread=lambda **_kwargs: worker)
    )

    with pytest.raises(TimeoutError) as excinfo:
        # The stand-in clock decides the budget, so this deadline is unused.
        client._run_blocking_io(lambda: "too late", deadline=0.0, cancel=cancel)

    assert worker.joins[0] == pytest.approx(0.05), (
        "the timed join did not run with the remaining budget"
    )
    assert str(excinfo.value) == "IPC client operation timed out", (
        "an exited worker must not be reported as wedged"
    )
    assert cancels == ["called"], "the deadline did not cancel the worker"


def test_join_with_timeout_reports_the_exact_timeout_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled worker still leaves the caller with the base timeout text."""
    monkeypatch.setattr(client, "IO_CANCEL_GRACE", 1.0)
    worker = _ControlledWorker(exits_on_cancel=True)
    cancels: list[str] = []

    def cancel() -> None:
        """Record the cancellation and end the worker."""
        cancels.append("called")
        worker.released = True

    with pytest.raises(TimeoutError) as excinfo:
        client._join_with_timeout_and_cancel(
            typ.cast("threading.Thread", worker), 0.01, cancel
        )

    assert worker.joins == [0.01], "the caller did not join for its budget"
    assert cancels == ["called"], "the timeout did not cancel the worker"
    assert not worker.is_alive(), (
        "the timeout was reported while the cancelled worker still ran"
    )
    assert str(excinfo.value) == "IPC client operation timed out", (
        "the timeout message changed"
    )


def test_join_with_timeout_names_a_wedged_worker_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker that ignores cancellation is named in the exact timeout text."""
    monkeypatch.setattr(client, "IO_CANCEL_GRACE", 0.01)
    worker = _ControlledWorker(exits_on_cancel=False)

    with pytest.raises(TimeoutError) as excinfo:
        client._join_with_timeout_and_cancel(
            typ.cast("threading.Thread", worker), 0.01, lambda: None
        )

    assert worker.joins, "the caller never joined the worker"
    assert worker.joins[0] == pytest.approx(0.01), (
        "the caller did not join for its budget"
    )
    grace_joins = worker.joins[1:]
    assert all(budget is not None and 0 < budget <= 0.05 for budget in grace_joins), (
        "the cancellation grace joined without a bounded timeout"
    )
    assert str(excinfo.value) == (
        "IPC client operation timed out; the I/O worker did not exit after cancellation"
    ), "the wedged-worker suffix changed"


def test_cancel_and_await_worker_waits_for_a_real_worker_to_exit() -> None:
    """Cancellation is offered to a real worker and its exit is awaited."""
    worker, release = _event_blocked_worker()
    cancels: list[str] = []

    def cancel() -> None:
        """Record the cancellation and release the worker."""
        cancels.append("called")
        release.set()

    try:
        exited = client._cancel_and_await_worker(worker, cancel)
        assert not worker.is_alive(), (
            "the helper returned while the cancelled worker still ran"
        )
    finally:
        release.set()
    _join_bounded(worker)

    assert cancels == ["called"], "the worker was never cancelled"
    assert exited is True, "the helper did not observe the cancelled worker exit"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param("text", "text", id="string"),
        pytest.param(0, 0, id="zero"),
        pytest.param(False, False, id="false"),
        pytest.param([], [], id="empty-list"),
    ],
)
def test_extract_outcome_returns_falsey_values_unchanged(
    value: object, expected: object
) -> None:
    """Only a missing value becomes ``None``; falsey values are preserved."""
    assert client._extract_outcome({"value": value}) == expected, (
        "a falsey worker result was coerced"
    )


def test_extract_outcome_maps_missing_and_sentinel_values_to_none() -> None:
    """An absent or still-sentinel value is reported as ``None``."""
    assert client._extract_outcome({}) is None, "a missing value was not None"
    assert client._extract_outcome({"value": client._SENTINEL}) is None, (
        "the internal sentinel leaked to the caller"
    )


def test_extract_outcome_reraises_the_stored_error_first() -> None:
    """A stored error wins over any stored value and keeps its identity."""
    failure = ValueError("stored")
    outcome = {"error": failure, "value": "ignored"}

    with pytest.raises(ValueError, match="stored") as excinfo:
        client._extract_outcome(outcome)

    assert excinfo.value is failure, "the stored exception identity was lost"


def test_extract_outcome_treats_a_none_error_as_absent() -> None:
    """A ``None`` error is not raised; the value is returned instead."""
    assert client._extract_outcome({"error": None, "value": "ok"}) == "ok", (
        "a None error must not be raised"
    )


def test_join_with_timeout_returns_promptly_for_a_finished_worker() -> None:
    """A worker that has already exited is accepted without cancellation."""
    cancel_calls: list[str] = []

    def cancel() -> None:
        """Record an unexpected cancellation."""
        cancel_calls.append("called")

    worker = threading.Thread(target=lambda: None, daemon=True)
    worker.start()
    worker.join(_SAFETY_TIMEOUT)

    client._join_with_timeout_and_cancel(worker, 0.05, cancel)

    assert not cancel_calls, "a finished worker was cancelled"
