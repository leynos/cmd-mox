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
