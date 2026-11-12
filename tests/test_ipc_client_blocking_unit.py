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


def _sleeper(delay: float) -> threading.Thread:
    """Start a daemon worker that exits on its own after *delay* seconds.

    Self-terminating workers keep a mutant that joins without a timeout from
    hanging the suite: it waits out the sleep, then visibly skips the timeout.

    Returns
    -------
    threading.Thread
        The started daemon thread.
    """
    worker = threading.Thread(target=time.sleep, args=(delay,), daemon=True)
    worker.start()
    return worker


def _join_bounded(worker: threading.Thread) -> None:
    """Release-safe join used by test clean-up."""
    worker.join(_SAFETY_TIMEOUT)
    assert not worker.is_alive(), "the worker outlived the test"


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


def test_run_blocking_io_honours_its_deadline_budget() -> None:
    """A worker that outlives the deadline is cancelled, not waited on forever."""
    started = threading.Event()
    cancels: list[str] = []

    def cancel() -> None:
        """Record the cancellation."""
        cancels.append("called")

    def slow() -> str:
        """Finish after the deadline with a result the caller must not see."""
        started.set()
        time.sleep(0.15)
        return "too late"

    with pytest.raises(TimeoutError) as excinfo:
        client._run_blocking_io(slow, deadline=time.monotonic() + 0.05, cancel=cancel)

    assert str(excinfo.value) == "IPC client operation timed out", (
        "an exited worker must not be reported as wedged"
    )
    assert cancels == ["called"], "the deadline did not cancel the worker"
    assert started.is_set(), "the worker never ran"


def test_run_blocking_io_cancels_a_worker_when_the_deadline_is_already_spent() -> None:
    """An expired deadline cancels the just-started worker before raising."""
    release = threading.Event()
    started = threading.Event()
    cancels: list[str] = []

    def cancel() -> None:
        """Record the cancellation and release the worker."""
        cancels.append("called")
        release.set()

    def block() -> None:
        """Wait for the cancellation to release the worker."""
        started.set()
        release.wait(_SAFETY_TIMEOUT)

    try:
        with pytest.raises(TimeoutError):
            client._run_blocking_io(
                block, deadline=time.monotonic() - 1.0, cancel=cancel
            )
        assert cancels == ["called"], "the expired deadline did not cancel the worker"
        assert started.is_set(), "the worker never ran"
    finally:
        release.set()


def test_join_with_timeout_reports_the_exact_timeout_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelling worker still leaves the caller with the base timeout text."""
    monkeypatch.setattr(client, "IO_CANCEL_GRACE", 1.0)
    worker = _sleeper(0.15)

    with pytest.raises(TimeoutError) as excinfo:
        client._join_with_timeout_and_cancel(worker, 0.01, lambda: None)
    _join_bounded(worker)

    assert str(excinfo.value) == "IPC client operation timed out", (
        "the timeout message changed"
    )


def test_join_with_timeout_names_a_wedged_worker_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker that ignores cancellation is named in the exact timeout text."""
    monkeypatch.setattr(client, "IO_CANCEL_GRACE", 0.01)
    worker = _sleeper(0.2)

    with pytest.raises(TimeoutError) as excinfo:
        client._join_with_timeout_and_cancel(worker, 0.01, lambda: None)
    _join_bounded(worker)

    assert str(excinfo.value) == (
        "IPC client operation timed out; the I/O worker did not exit after cancellation"
    ), "the wedged-worker suffix changed"


def test_join_with_timeout_does_not_wait_past_its_budget() -> None:
    """A worker finishing after the budget still times out promptly."""
    finished = threading.Event()

    def slow() -> None:
        """Finish shortly after the join budget without needing cancellation."""
        time.sleep(0.15)
        finished.set()

    worker = threading.Thread(target=slow, daemon=True)
    worker.start()
    started = time.monotonic()

    try:
        with pytest.raises(TimeoutError):
            client._join_with_timeout_and_cancel(worker, 0.01, lambda: None)
        elapsed = time.monotonic() - started
        assert elapsed < 1.0, "the join waited for the worker instead of its budget"
    finally:
        _join_bounded(worker)
    assert finished.is_set(), "the slow worker never finished"


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


def test_validate_initial_deadline_returns_the_remaining_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live deadline returns the remaining budget without cancelling."""
    monkeypatch.setattr(client, "_remaining_time", lambda _deadline: 0.75)
    cancels: list[tuple[object, object]] = []
    monkeypatch.setattr(
        client, "_cancel_and_await_worker", lambda *args: cancels.append(args)
    )

    def cancel() -> None:
        """Stand in for a cancellation callback."""

    thread = threading.Thread(target=cancel)

    remaining = client._validate_initial_deadline(2.0, cancel, thread)

    assert remaining == pytest.approx(0.75), "the remaining budget was not forwarded"
    assert not cancels, "a live deadline cancelled the worker"


def test_validate_initial_deadline_cancels_the_exact_worker_before_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired deadline cancels the supplied worker and cancel callback."""

    def expired(_deadline: float) -> float:
        """Report an exhausted deadline."""
        raise TimeoutError

    monkeypatch.setattr(client, "_remaining_time", expired)
    cancels: list[tuple[object, object]] = []
    monkeypatch.setattr(
        client, "_cancel_and_await_worker", lambda *args: cancels.append(args)
    )

    def cancel() -> None:
        """Stand in for a cancellation callback."""

    thread = threading.Thread(target=cancel)

    with pytest.raises(TimeoutError):
        client._validate_initial_deadline(2.0, cancel, thread)

    assert cancels == [(thread, cancel)], (
        "the expired deadline did not cancel the worker pair"
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
