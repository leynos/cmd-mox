"""Tests for the named-pipe transport's concurrency, size, and time bounds.

The transport is Windows-only, so these tests drive it through the scriptable
``pywin32`` fakes in :mod:`cmd_mox.unittests._named_pipe_fakes`. They cover
admission control, permit accounting, the message-size limit, and the
cancellable deadline-aware read, plus the bounded events each path emits.
"""

from __future__ import annotations

import functools
import pathlib
import threading
import time
import typing as typ

import pytest

from cmd_mox.ipc import _observability, named_pipe
from cmd_mox.ipc._named_pipe_limits import (
    WORKER_OPERATION,
    ClientSlot,
    PipeReadCancelled,
    acquire_client_slot,
    remaining_ms,
)
from cmd_mox.ipc.windows import ERROR_IO_PENDING, PipeMessageTooLargeError
from cmd_mox.unittests._named_pipe_fakes import (  # ruff: ignore[unused-import] - re-exported pytest fixtures
    UNEXPECTED_WINERROR,
    FakeEventHandle,
    FakeWin32Event,
    FakeWin32File,
    FakeWin32Pipe,
    FakeWinError,
    PatchWin32,
    ScriptedRead,
    build_state,
    closed_pipe_handles,
    patch_win32_fixture,
    windows_platform_fixture,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Interleavings tried when racing an admission against ``stop``.
_RACE_ATTEMPTS: typ.Final[int] = 40


def _no_gate() -> None:
    """Release an admitting thread immediately, with no rendezvous."""


#: Fields an event may never carry: they would be derived from the payload.
_FORBIDDEN_FIELDS: typ.Final[tuple[str, ...]] = (
    "kind",
    "correlation_id",
    "attempt",
)


def _worker_events(
    events: cabc.Sequence[_observability.IPCEvent], outcome: str
) -> list[_observability.IPCEvent]:
    """Return captured worker events matching *outcome*.

    Returns
    -------
    list[cmd_mox.ipc._observability.IPCEvent]
        The matching events, in emission order.
    """
    return [
        event
        for event in events
        if event.operation == WORKER_OPERATION and event.outcome == outcome
    ]


def _assert_bounded(event: _observability.IPCEvent) -> None:
    """Assert *event* carries no payload-derived dimension."""
    assert event.transport == "named_pipe", "transport dimension missing"
    for field in _FORBIDDEN_FIELDS:
        assert getattr(event, field) is None, f"{field} must not be set"


def _run_worker(state: named_pipe._NamedPipeState, handle: object) -> ClientSlot:
    """Serve one client on a worker thread and wait for it to finish.

    Asserts that a permit was available and that the worker thread finished.

    Returns
    -------
    ClientSlot
        The permit handed to the worker, so callers can assert on its release.
    """
    slot = acquire_client_slot(state._client_slots)
    assert slot is not None, "no permit was available"
    state._spawn_handler_thread(handle, slot)
    for thread in state._get_active_threads():
        thread.join(5.0)
        assert not thread.is_alive(), "worker thread did not finish"
    return slot


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


# ---------------------------------------------------------------------------
# Message-size bound
# ---------------------------------------------------------------------------


@pytest.fixture(name="small_limits")
def small_limits_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shrink the chunk and message limits so tests stay cheap."""
    monkeypatch.setattr(named_pipe, "PIPE_CHUNK_SIZE", 4)
    monkeypatch.setattr(named_pipe, "MAX_MESSAGE_SIZE", 8)


@pytest.mark.usefixtures("small_limits")
def test_read_request_rejects_an_oversized_multi_chunk_message(
    patch_win32: PatchWin32,
) -> None:
    """Three four-byte chunks exceed an eight-byte limit and are refused."""
    patch_win32(
        FakeWin32File(
            reads=[
                ScriptedRead(data=b"abcd", more=True),
                ScriptedRead(data=b"efgh", more=True),
                ScriptedRead(data=b"ijkl"),
            ]
        )
    )
    state = build_state()

    with pytest.raises(PipeMessageTooLargeError) as excinfo:
        state._read_request(object())

    assert excinfo.value.received == 12, "byte count not reported"
    assert excinfo.value.limit == 8, "limit not reported"


@pytest.mark.usefixtures("small_limits")
def test_read_request_accepts_a_message_exactly_at_the_limit(
    patch_win32: PatchWin32,
) -> None:
    """Two four-byte chunks meet the eight-byte limit exactly."""
    patch_win32(
        FakeWin32File(
            reads=[
                ScriptedRead(data=b"abcd", more=True),
                ScriptedRead(data=b"efgh"),
            ]
        )
    )

    assert build_state()._read_request(object()) == b"abcdefgh", "boundary rejected"


def test_oversized_read_is_rejected_without_dispatch(
    patch_win32: PatchWin32, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An oversized message is refused before the request pipeline runs."""
    file_fake, pipe_fake = patch_win32()
    state = build_state()
    state._client_slots = threading.BoundedSemaphore(1)
    dispatched: list[bytes] = []

    def reject_read(_handle: object) -> bytes:
        raise PipeMessageTooLargeError(received=12, limit=8)

    monkeypatch.setattr(state, "_read_request", reject_read)
    monkeypatch.setattr(
        named_pipe, "_request_pipeline", lambda _outer, raw, _t: dispatched.append(raw)
    )
    handle = object()

    with _observability.capture_events() as events:
        slot = _run_worker(state, handle)

    rejected = _worker_events(events, "read_size_rejected")
    assert len(rejected) == 1, "the oversize rejection was not reported"
    assert rejected[0].message_size == 12, "message size was not reported"
    assert rejected[0].error_category == "PipeMessageTooLargeError", "wrong category"
    assert rejected[0].duration_ms is None, "rejection must not report a duration"
    _assert_bounded(rejected[0])
    assert not dispatched, "an oversized request reached the pipeline"
    assert slot.released, "permit was not released"
    assert pipe_fake.disconnected == [handle], "handle was not disconnected"
    assert closed_pipe_handles(file_fake) == [handle], "handle was not closed"


# ---------------------------------------------------------------------------
# Cancellable, deadline-aware read
# ---------------------------------------------------------------------------


def test_read_chunk_waits_for_a_pending_overlapped_read(
    patch_win32: PatchWin32,
) -> None:
    """A pending read is awaited and then collected."""
    event_fake = FakeWin32Event()
    file_fake, _pipe_fake = patch_win32(
        FakeWin32File(reads=[ScriptedRead(data=b"hi", status=ERROR_IO_PENDING)]),
        win32event=event_fake,
    )
    state = build_state()

    result = state._read_chunk(object(), time.monotonic() + 5.0, 8)

    assert result == (0, b"hi"), "read result mismatch"
    assert len(event_fake.waits) == 1, "the pending read was not awaited"
    assert not file_fake.cancelled, "a completed read was cancelled"
    assert file_fake.closed, "the overlapped event handle leaked"


def test_read_chunk_wait_covers_the_deadline_and_the_stop_handle(
    patch_win32: PatchWin32,
) -> None:
    """The wait watches both the read event and the shutdown event."""
    event_fake = FakeWin32Event()
    patch_win32(
        FakeWin32File(reads=[ScriptedRead(data=b"hi", status=ERROR_IO_PENDING)]),
        win32event=event_fake,
    )
    state = build_state()

    state._read_chunk(object(), time.monotonic() + 5.0, 8)

    handles, wait_all, timeout_ms = event_fake.waits[0]
    assert len(handles) == 2, "the shutdown event was not part of the wait"
    assert handles[1] is state._stop_handle, "the stop handle was not awaited"
    assert wait_all is False, "the wait must return on the first signal"
    assert 0 < timeout_ms <= 5000, "the deadline was not converted to a timeout"


@pytest.mark.parametrize(
    ("wait_result", "timed_out"),
    [
        (FakeWin32Event.WAIT_TIMEOUT, True),
        (FakeWin32Event.WAIT_OBJECT_0 + 1, False),
    ],
    ids=["deadline-expired", "server-stopped"],
)
def test_read_chunk_cancels_and_drains_on_wait_failure(
    patch_win32: PatchWin32,
    wait_result: int,
    timed_out: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - parametrized expectation, not an API flag
) -> None:
    """A deadline or a stop cancels the read in the reading thread itself."""
    file_fake, _pipe_fake = patch_win32(
        FakeWin32File(reads=[ScriptedRead(data=b"hi", status=ERROR_IO_PENDING)]),
        win32event=FakeWin32Event([wait_result]),
    )
    state = build_state()
    handle = object()

    with pytest.raises(PipeReadCancelled) as excinfo:
        state._read_chunk(handle, time.monotonic() + 5.0, 8)

    assert excinfo.value.timed_out is timed_out, "cancellation cause mismatch"
    assert file_fake.cancelled == [handle], "the pending read was not cancelled"
    assert any(isinstance(h, FakeEventHandle) for h in file_fake.closed), (
        "the overlapped event handle leaked"
    )


def test_blocked_client_read_times_out_and_cleans_up(
    patch_win32: PatchWin32,
) -> None:
    """A silent client is timed out; its worker finishes and frees its permit."""
    file_fake, pipe_fake = patch_win32(
        FakeWin32File(reads=[ScriptedRead(status=ERROR_IO_PENDING)]),
        win32event=FakeWin32Event([FakeWin32Event.WAIT_TIMEOUT]),
    )
    state = build_state()
    state._client_slots = threading.BoundedSemaphore(1)
    handle = object()

    with _observability.capture_events() as events:
        slot = _run_worker(state, handle)

    timed_out = _worker_events(events, "timeout")
    assert len(timed_out) == 1, "the timeout was not reported"
    assert timed_out[0].error_category == "ReadTimeout", "wrong timeout category"
    assert timed_out[0].message_size is None, "timeout must not report a size"
    _assert_bounded(timed_out[0])
    assert file_fake.cancelled == [handle], "the blocked read was not cancelled"
    assert pipe_fake.disconnected == [handle], "handle was not disconnected"
    assert handle in file_fake.closed, "handle was not closed"
    assert slot.released, "permit was not released"
    assert state._client_slots.acquire(blocking=False), "capacity was not returned"
    assert not state._get_active_threads(), "the worker thread was left running"


def test_shutdown_cancelled_read_is_reported_as_a_stopped_worker(
    patch_win32: PatchWin32,
) -> None:
    """A read cancelled by ``stop`` completes rather than reporting a timeout."""
    patch_win32(
        FakeWin32File(reads=[ScriptedRead(status=ERROR_IO_PENDING)]),
        win32event=FakeWin32Event([FakeWin32Event.WAIT_OBJECT_0 + 1]),
    )
    state = build_state()
    state._client_slots = threading.BoundedSemaphore(1)

    with _observability.capture_events() as events:
        slot = _run_worker(state, object())

    completed = _worker_events(events, "completed")
    assert len(completed) == 1, "shutdown cancellation was not reported"
    assert completed[0].error_category == "ServerStopped", "wrong category"
    assert not _worker_events(events, "timeout"), "shutdown reported as a timeout"
    assert slot.released, "permit was not released"


def test_read_chunk_treats_a_more_data_completion_as_a_continuation(
    patch_win32: PatchWin32,
) -> None:
    """``GetOverlappedResult`` raising ``ERROR_MORE_DATA`` continues the message."""
    event_fake = FakeWin32Event()
    patch_win32(
        FakeWin32File(
            reads=[
                ScriptedRead(data=b"hi", error=FakeWinError(named_pipe.ERROR_MORE_DATA))
            ]
        ),
        win32event=event_fake,
    )
    state = build_state()

    status, data = state._read_chunk(object(), time.monotonic() + 5.0, 2)

    assert status == named_pipe.ERROR_MORE_DATA, "continuation status lost"
    assert data == b"hi", "a filled buffer must be returned in full"


def test_read_chunk_treats_a_pending_readfile_error_as_a_pending_read(
    patch_win32: PatchWin32,
) -> None:
    """``ReadFile`` raising ``ERROR_IO_PENDING`` still awaits completion."""
    event_fake = FakeWin32Event()
    patch_win32(
        FakeWin32File(
            reads=[ScriptedRead(data=b"hi", start_error=FakeWinError(ERROR_IO_PENDING))]
        ),
        win32event=event_fake,
    )
    state = build_state()

    status, data = state._read_chunk(object(), time.monotonic() + 5.0, 2)

    assert status == 0, "a completed pending read must report success"
    assert data == b"hi", "the awaited read did not deliver its data"
    assert len(event_fake.waits) == 1, "the pending read was not awaited"


def test_read_chunk_propagates_an_unexpected_readfile_error(
    patch_win32: PatchWin32,
) -> None:
    """Any other ``ReadFile`` failure reaches the caller unchanged."""
    patch_win32(
        FakeWin32File(
            reads=[ScriptedRead(start_error=FakeWinError(UNEXPECTED_WINERROR))]
        ),
        win32event=FakeWin32Event(),
    )
    state = build_state()

    with pytest.raises(FakeWinError) as excinfo:
        state._read_chunk(object(), time.monotonic() + 5.0, 2)

    assert excinfo.value.winerror == UNEXPECTED_WINERROR, "wrong error propagated"


# ---------------------------------------------------------------------------
# Startup failure reporting
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("windows_platform")
def test_accept_failure_at_startup_is_reported_to_the_waiting_starter(
    patch_win32: PatchWin32,
) -> None:
    """A server whose first pipe instance fails must not report a clean start."""
    patch_win32(
        win32pipe=FakeWin32Pipe(handles=[FakeWinError(UNEXPECTED_WINERROR)]),
        win32event=FakeWin32Event(),
    )
    server = named_pipe.NamedPipeServer(pathlib.Path("ipc.sock"), timeout=1.0)

    with pytest.raises(RuntimeError, match="failed to create its first pipe instance"):
        server.start()

    state = server._server
    assert state is not None, "state was discarded"
    assert state.startup_failed, "failure was not recorded"


def test_remaining_ms_floors_at_zero() -> None:
    """An expired deadline yields a zero timeout rather than a negative one."""
    assert remaining_ms(0.0) == 0, "negative timeout"


# ---------------------------------------------------------------------------
# Shutdown wiring
# ---------------------------------------------------------------------------


def test_stop_signals_an_existing_win32_stop_handle(patch_win32: PatchWin32) -> None:
    """A shutdown wakes threads already waiting on the Win32 event."""
    event_fake = FakeWin32Event()
    patch_win32(
        FakeWin32File(FakeWinError(named_pipe.ERROR_FILE_NOT_FOUND)),
        win32event=event_fake,
    )
    state = build_state()
    handle = state._win32_stop_handle()

    state.stop()

    assert event_fake.signalled == [handle], "the stop handle was not signalled"


def test_win32_stop_handle_is_presignalled_after_stop(
    patch_win32: PatchWin32,
) -> None:
    """A handle created after ``stop`` starts out signalled."""
    event_fake = FakeWin32Event()
    patch_win32(
        FakeWin32File(FakeWinError(named_pipe.ERROR_FILE_NOT_FOUND)),
        win32event=event_fake,
    )
    state = build_state()
    state.stop()

    handle = state._win32_stop_handle()

    assert event_fake.signalled == [handle], "a late handle missed the wakeup"


# ---------------------------------------------------------------------------
# Overlapped connect
# ---------------------------------------------------------------------------


def test_try_connect_pipe_awaits_a_pending_connection(
    patch_win32: PatchWin32,
) -> None:
    """A pending ``ConnectNamedPipe`` is awaited before serving the client."""
    event_fake = FakeWin32Event()
    file_fake, _pipe_fake = patch_win32(
        win32pipe=FakeWin32Pipe(connect_results=[ERROR_IO_PENDING]),
        win32event=event_fake,
    )
    handle = object()

    assert build_state()._try_connect_pipe(handle) == (True, True), "bad decision"
    assert len(event_fake.waits) == 1, "the pending connect was not awaited"
    assert closed_pipe_handles(file_fake) == [], "the client handle was closed"


def test_try_connect_pipe_stops_when_shutdown_wins_the_race(
    patch_win32: PatchWin32,
) -> None:
    """A shutdown during accept cancels the connect and ends the loop."""
    file_fake, _pipe_fake = patch_win32(
        win32pipe=FakeWin32Pipe(connect_results=[ERROR_IO_PENDING]),
        win32event=FakeWin32Event([FakeWin32Event.WAIT_OBJECT_0 + 1]),
    )
    handle = object()

    assert build_state()._try_connect_pipe(handle) == (False, False), "bad decision"
    assert file_fake.cancelled == [handle], "the pending connect was not cancelled"
    assert closed_pipe_handles(file_fake) == [handle], "handle was not closed"


def test_try_connect_pipe_maps_a_failed_overlapped_connect(
    patch_win32: PatchWin32,
) -> None:
    """A connect that fails on completion is mapped like a direct failure."""
    file_fake, _pipe_fake = patch_win32(
        FakeWin32File(reads=[]),
        win32pipe=FakeWin32Pipe(connect_results=[ERROR_IO_PENDING]),
        win32event=FakeWin32Event(),
    )
    state = build_state()
    handle = object()

    def fail_result(*_args: object) -> int:
        raise FakeWinError(UNEXPECTED_WINERROR)

    file_fake.GetOverlappedResult = fail_result  # type: ignore[method-assign, ty:invalid-assignment]

    assert state._try_connect_pipe(handle) == (True, False), "bad decision"
    assert closed_pipe_handles(file_fake) == [handle], "handle was not closed"
