"""Tests for the named-pipe transport's message-size and read-deadline bounds.

The transport is Windows-only, so these tests drive it through the scriptable
``pywin32`` fakes in :mod:`cmd_mox.unittests._named_pipe_fakes`. They cover the
maximum message size, the chunked overlapped read, and the cancellable
deadline-aware wait, plus the bounded events each path emits.
"""

from __future__ import annotations

import threading
import time

import pytest

from cmd_mox.ipc import _observability, named_pipe
from cmd_mox.ipc._named_pipe_limits import PipeReadCancelled
from cmd_mox.ipc.windows import ERROR_IO_PENDING, PipeMessageTooLargeError
from cmd_mox.unittests._named_pipe_fakes import (  # ruff: ignore[unused-import] - re-exported pytest fixtures
    UNEXPECTED_WINERROR,
    FakeEventHandle,
    FakeWin32Event,
    FakeWin32File,
    FakeWinError,
    PatchWin32,
    ScriptedRead,
    build_state,
    closed_pipe_handles,
    patch_win32_fixture,
)
from cmd_mox.unittests._named_pipe_test_helpers import (
    _assert_bounded,
    _run_worker,
    _worker_events,
)

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
