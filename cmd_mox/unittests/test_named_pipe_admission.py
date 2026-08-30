"""Tests for the named-pipe transport's admission control and permit accounting.

The transport is Windows-only, so these tests drive it through the scriptable
``pywin32`` fakes in :mod:`cmd_mox.unittests._named_pipe_fakes`. They cover the
active-client limit, the permit lifecycle across success, failure and disposal
paths, and the races between admission and shutdown.
"""

from __future__ import annotations

import functools
import threading
import typing as typ

import pytest

from cmd_mox.ipc import _observability, named_pipe
from cmd_mox.ipc._named_pipe_limits import acquire_client_slot
from cmd_mox.unittests._named_pipe_fakes import (  # ruff: ignore[unused-import] - re-exported pytest fixtures
    UNEXPECTED_WINERROR,
    FakeWin32File,
    FakeWin32Pipe,
    FakeWinError,
    PatchWin32,
    build_state,
    closed_pipe_handles,
    patch_win32_fixture,
    windows_platform_fixture,
)
from cmd_mox.unittests._named_pipe_test_helpers import (
    _assert_bounded,
    _run_worker,
    _worker_events,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Interleavings tried when racing an admission against ``stop``.
_RACE_ATTEMPTS: typ.Final[int] = 40


# ---------------------------------------------------------------------------
# Permit accounting
# ---------------------------------------------------------------------------


def test_client_slot_releases_exactly_once() -> None:
    """A slot returns its permit once no matter how often it is released."""
    semaphore = threading.BoundedSemaphore(1)
    slot = acquire_client_slot(semaphore)
    assert slot is not None, "the first permit was refused"
    assert semaphore.acquire(blocking=False) is False, "permit was not taken"

    slot.release()
    slot.release()

    assert slot.released, "slot did not latch"
    assert semaphore.acquire(blocking=False) is True, "permit was not returned"


def test_acquire_client_slot_returns_none_at_capacity() -> None:
    """Acquisition fails without blocking once the limit is reached."""
    semaphore = threading.BoundedSemaphore(1)

    assert acquire_client_slot(semaphore) is not None, "first client refused"
    assert acquire_client_slot(semaphore) is None, "limit was not enforced"


def test_state_uses_the_configured_client_limit() -> None:
    """The state's semaphore is sized by the module-level limit."""
    state = build_state()

    acquired = [
        acquire_client_slot(state._client_slots)
        for _ in range(named_pipe.MAX_ACTIVE_CLIENTS)
    ]

    assert all(slot is not None for slot in acquired), "limit is too small"
    assert acquire_client_slot(state._client_slots) is None, "limit not enforced"


# ---------------------------------------------------------------------------
# Admission control
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("windows_platform")
def test_serve_forever_rejects_clients_beyond_the_limit(
    patch_win32: PatchWin32,
) -> None:
    """A client arriving at capacity is refused and the loop keeps serving."""
    handle = object()
    file_fake, pipe_fake = patch_win32(
        win32pipe=FakeWin32Pipe(handles=[handle, FakeWinError(UNEXPECTED_WINERROR)])
    )
    state = build_state()
    # Exhaust the pool so the very next client must be refused.
    state._client_slots = threading.BoundedSemaphore(1)
    state._client_slots.acquire()

    with _observability.capture_events() as events:
        state.serve_forever()

    rejected = _worker_events(events, "rejected")
    assert len(rejected) == 1, "the over-limit client was not rejected"
    assert rejected[0].error_category == "ClientLimitReached", "wrong category"
    assert rejected[0].message_size is None, "rejection must not report a size"
    _assert_bounded(rejected[0])
    assert pipe_fake.disconnected == [handle], "handle was not disconnected"
    assert closed_pipe_handles(file_fake) == [handle], "handle was not closed"
    assert len(pipe_fake.create_calls) == 2, "the accept loop stopped serving"
    assert not state._get_active_threads(), "a worker was spawned anyway"


def test_admit_client_releases_the_permit_when_the_thread_cannot_start(
    patch_win32: PatchWin32, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A thread that never starts must not strand its permit."""
    file_fake, pipe_fake = patch_win32()
    state = build_state()
    state._client_slots = threading.BoundedSemaphore(1)

    def refuse_thread(_handle: object, _slot: object = None) -> None:
        msg = "can't start new thread"
        raise RuntimeError(msg)

    monkeypatch.setattr(state, "_spawn_handler_thread", refuse_thread)
    handle = object()

    with _observability.capture_events() as events:
        state._admit_client(handle)

    rejected = _worker_events(events, "rejected")
    assert len(rejected) == 1, "the failed spawn was not reported"
    assert rejected[0].error_category == "ThreadStartFailed", "wrong category"
    _assert_bounded(rejected[0])
    assert state._client_slots.acquire(blocking=False), "permit was not released"
    assert pipe_fake.disconnected == [handle], "handle was not disconnected"
    assert closed_pipe_handles(file_fake) == [handle], "handle was not closed"


def test_admit_client_refuses_a_client_arriving_after_stop(
    patch_win32: PatchWin32,
) -> None:
    """Shutdown closes admission: a late client is refused, not registered."""
    file_fake, pipe_fake = patch_win32(
        FakeWin32File(FakeWinError(named_pipe.ERROR_FILE_NOT_FOUND))
    )
    state = build_state()
    state._client_slots = threading.BoundedSemaphore(1)
    state.stop()
    handle = object()

    with _observability.capture_events() as events:
        state._admit_client(handle)

    rejected = _worker_events(events, "rejected")
    assert len(rejected) == 1, "the late client was not rejected"
    assert rejected[0].error_category == "ServerStopping", "wrong category"
    _assert_bounded(rejected[0])
    assert not state._get_active_threads(), "a worker was registered after stop"
    assert state._client_slots.acquire(blocking=False), "permit was not released"
    assert pipe_fake.disconnected == [handle], "handle was not disconnected"
    assert closed_pipe_handles(file_fake) == [handle], "handle was not closed"


def _admit_on_thread(
    state: named_pipe._NamedPipeState, gate: cabc.Callable[[], object]
) -> threading.Thread:
    """Start a thread that admits one client once *gate* releases it.

    Returns
    -------
    threading.Thread
        The started admitting thread.
    """

    def admit() -> None:
        gate()
        state._admit_client(object())

    thread = threading.Thread(target=admit)
    thread.start()
    return thread


def _no_gate() -> None:
    """Release an admitting thread immediately, with no rendezvous."""


@pytest.mark.parametrize(
    "synchronised",
    [pytest.param(True, id="barrier"), pytest.param(False, id="free-running")],
)
def test_admission_racing_stop_is_refused_or_joined(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
    synchronised: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - parametrized scheduling strategy, not an API flag
) -> None:
    """A client admitted alongside ``stop`` is refused or joined, never left.

    Registration and shutdown take the same lock, so every attempt resolves one
    way or the other: the admission is refused outright, or the worker is
    registered before ``stop`` and therefore appears in the snapshot
    ``join_clients`` awaits. Rendezvousing on a barrier lets shutdown win the
    race; letting the admitting thread run free lets admission win, so the two
    parameters cover both interleavings.
    """
    patch_win32(FakeWin32File(FakeWinError(named_pipe.ERROR_FILE_NOT_FOUND)))
    for _ in range(_RACE_ATTEMPTS):
        state = build_state()
        monkeypatch.setattr(state, "_read_request", lambda _handle: None)
        barrier = threading.Barrier(2)
        gate = functools.partial(barrier.wait, 5.0) if synchronised else _no_gate

        with _observability.capture_events() as events:
            admitter = _admit_on_thread(state, gate)
            if synchronised:
                barrier.wait(5.0)
            state.stop()
            admitter.join(5.0)
            state.join_clients(5.0)

        assert not admitter.is_alive(), "the admitting thread stalled"
        assert not state._get_active_threads(), "a worker outlived shutdown"
        admitted = _worker_events(events, "admitted")
        rejected = _worker_events(events, "rejected")
        assert len(admitted) + len(rejected) == 1, "the admission was ambiguous"
        assert len(_worker_events(events, "completed")) == len(admitted), (
            "an admitted worker never finished"
        )


def test_a_worker_admitted_before_stop_is_joined(
    patch_win32: PatchWin32, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``join_clients`` awaits a worker registered before shutdown began."""
    patch_win32(FakeWin32File(FakeWinError(named_pipe.ERROR_FILE_NOT_FOUND)))
    state = build_state()
    serving = threading.Event()
    release = threading.Event()

    def block_read(_handle: object) -> bytes | None:
        serving.set()
        assert release.wait(5.0), "the blocked worker was never released"
        return None

    monkeypatch.setattr(state, "_read_request", block_read)
    state._admit_client(object())
    assert serving.wait(5.0), "the worker never began serving"
    [worker] = state._get_active_threads()

    state.stop()
    release.set()
    state.join_clients(5.0)

    assert not worker.is_alive(), "shutdown returned with a worker still running"
    assert not state._get_active_threads(), "the worker was not untracked"


def test_completed_client_releases_capacity(
    patch_win32: PatchWin32, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A normal completion returns its permit and reports a duration."""
    file_fake, _pipe_fake = patch_win32()
    state = build_state()
    state._client_slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(state, "_read_request", lambda _handle: b"{}")
    monkeypatch.setattr(named_pipe, "_request_pipeline", lambda *_args: None)
    handle = object()

    with _observability.capture_events() as events:
        slot = _run_worker(state, handle)

    completed = _worker_events(events, "completed")
    assert len(completed) == 1, "completion was not reported"
    assert completed[0].error_category is None, "a clean run reported an error"
    assert completed[0].duration_ms is not None, "duration was not measured"
    _assert_bounded(completed[0])
    assert _worker_events(events, "admitted"), "admission was not reported"
    assert slot.released, "permit was not released"
    assert state._client_slots.acquire(blocking=False), "capacity was not returned"
    assert closed_pipe_handles(file_fake) == [handle], "handle was not closed"


def test_read_failure_releases_capacity(
    patch_win32: PatchWin32, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed read still returns its permit and closes the handle."""
    file_fake, pipe_fake = patch_win32()
    state = build_state()
    state._client_slots = threading.BoundedSemaphore(1)

    def fail_read(_handle: object) -> bytes:
        raise FakeWinError(UNEXPECTED_WINERROR)

    monkeypatch.setattr(state, "_read_request", fail_read)
    handle = object()

    with _observability.capture_events() as events:
        slot = _run_worker(state, handle)

    completed = _worker_events(events, "completed")
    assert len(completed) == 1, "the failure was not reported"
    assert completed[0].error_category == "PipeError", "wrong failure category"
    _assert_bounded(completed[0])
    assert slot.released, "permit was not released"
    assert state._client_slots.acquire(blocking=False), "capacity was not returned"
    assert pipe_fake.disconnected == [handle], "handle was not disconnected"
    assert closed_pipe_handles(file_fake) == [handle], "handle was not closed"


def test_failing_handle_disposal_still_releases_capacity(
    patch_win32: PatchWin32, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CloseHandle failure must not strand the client's permit.

    The cancel-and-drain path may already have closed the handle, so disposal
    can raise; leaking the permit each time would exhaust capacity for good.
    """
    file_fake, _pipe_fake = patch_win32()
    state = build_state()
    state._client_slots = threading.BoundedSemaphore(1)

    def fail_close(_handle: object) -> None:
        raise FakeWinError(UNEXPECTED_WINERROR)

    monkeypatch.setattr(file_fake, "CloseHandle", fail_close)
    monkeypatch.setattr(state, "_read_request", lambda _handle: None)

    slot = _run_worker(state, object())

    assert slot.released, "permit was not released"
    assert state._client_slots.acquire(blocking=False), "capacity was not returned"
