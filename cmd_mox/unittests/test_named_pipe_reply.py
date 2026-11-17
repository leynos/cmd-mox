"""Tests for bounded reply delivery on the named-pipe transport.

The transport is Windows-only, so every test drives it through the scriptable
``pywin32`` fakes. Each case scripts the overlapped write and the peer-close
probe read, so the delivered bytes, the confirmation, the cancellation path,
and the bounded worker event can all be asserted without native pipes.
"""

from __future__ import annotations

import logging
import time

import pytest

from cmd_mox.ipc import _observability, named_pipe
from cmd_mox.ipc._server_core import _RequestResult
from cmd_mox.ipc.windows import (
    ERROR_BROKEN_PIPE,
    ERROR_IO_PENDING,
    ERROR_NO_DATA,
    PIPE_CHUNK_SIZE,
)
from cmd_mox.unittests._named_pipe_fakes import (  # ruff: ignore[unused-import] - re-exported pytest fixtures
    FakeEventHandle,
    FakeWin32Event,
    FakeWin32File,
    FakeWinError,
    PatchWin32,
    ScriptedRead,
    ScriptedWrite,
    build_state,
    closed_pipe_handles,
    fake_backend_fixture,
    patch_win32_fixture,
)
from cmd_mox.unittests._named_pipe_test_helpers import (
    _assert_bounded,
    _run_worker,
    _worker_events,
)


def _answer_with(monkeypatch: pytest.MonkeyPatch, response: bytes | None) -> None:
    """Make the request pipeline answer with *response* bytes."""

    def pipeline(_outer: object, _raw: bytes, _transport: str) -> _RequestResult:
        """Return a canned pipeline result.

        Returns
        -------
        _RequestResult
            The canned response and no correlation identifier.
        """
        return _RequestResult(response_bytes=response, correlation_id=None)

    monkeypatch.setattr(named_pipe, "_request_pipeline", pipeline)


def _patch_request(
    monkeypatch: pytest.MonkeyPatch,
    state: named_pipe._NamedPipeState,
    raw: bytes | None = b"request",
) -> list[float]:
    """Replace the request read, recording the deadline it was given.

    Returns
    -------
    list[float]
        Collects the deadline passed to each request read.
    """
    deadlines: list[float] = []

    def read(_handle: object, deadline: float) -> bytes | None:
        """Record the deadline and return the canned request."""
        deadlines.append(deadline)
        return raw

    monkeypatch.setattr(state, "_read_request", read)
    return deadlines


def _assert_no_error_log(caplog: pytest.LogCaptureFixture) -> None:
    """Assert the reply failure stayed out of the error log."""
    assert not [
        record
        for record in caplog.records
        if record.name == "cmd_mox.ipc.named_pipe" and record.levelno >= logging.ERROR
    ], "a bounded reply failure was logged as an error"


def _assert_single_completion(
    events: list[_observability.IPCEvent], category: str | None
) -> _observability.IPCEvent:
    """Assert exactly one completed event carries *category*.

    Returns
    -------
    _observability.IPCEvent
        The completed event the worker emitted.
    """
    [completed] = _worker_events(events, "completed")
    assert completed.error_category == category, "wrong completion category"
    _assert_bounded(completed)
    return completed


def test_reply_delivery_confirms_a_client_close(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The peer-close probe confirms a completed reply."""
    file_fake, pipe_fake = patch_win32(
        FakeWin32File(
            writes=[ScriptedWrite()],
            reads=[ScriptedRead(error=FakeWinError(ERROR_BROKEN_PIPE))],
        )
    )
    state = build_state()
    handle = object()
    _answer_with(monkeypatch, b"reply")
    _patch_request(monkeypatch, state)

    with _observability.capture_events() as events:
        slot = _run_worker(state, handle)

    _assert_single_completion(events, None)
    assert [(h, payload) for h, payload, _ in file_fake.overlapped_writes] == [
        (handle, b"reply")
    ], "the reply did not reach the overlapped write"
    assert file_fake.read_calls == 1, "delivery was not confirmed by a probe read"
    assert (
        len([
            handle for handle in file_fake.closed if isinstance(handle, FakeEventHandle)
        ])
        == 2
    ), "an overlapped event handle was leaked"
    assert closed_pipe_handles(file_fake) == [handle], "the client handle leaked"
    assert pipe_fake.disconnected == [handle], "the client was not disconnected"
    assert slot.released, "the permit was not released"


def test_reply_larger_than_one_pipe_chunk_is_one_message(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reply past the chunk size is written as one message, not fragments.

    The client reads with the same chunk size and continues while Windows
    reports ``ERROR_MORE_DATA``, so splitting the reply into separate
    ``WriteFile`` calls would deliver only its first chunk.
    """
    payload = b"x" * (PIPE_CHUNK_SIZE + 1)
    file_fake, _pipe_fake = patch_win32(
        FakeWin32File(
            writes=[ScriptedWrite()],
            reads=[ScriptedRead(error=FakeWinError(ERROR_BROKEN_PIPE))],
        )
    )
    state = build_state()
    _answer_with(monkeypatch, payload)
    _patch_request(monkeypatch, state)

    with _observability.capture_events() as events:
        _run_worker(state, object())

    _assert_single_completion(events, None)
    [(_handle, written, _overlapped)] = file_fake.overlapped_writes
    assert written == payload, "the reply was split or truncated across writes"


def test_pending_reply_write_uses_the_connection_budget(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pending write is awaited under the shared connection deadline."""
    event_fake = FakeWin32Event()
    patch_win32(
        FakeWin32File(
            writes=[ScriptedWrite(status=ERROR_IO_PENDING)],
            reads=[ScriptedRead(error=FakeWinError(ERROR_BROKEN_PIPE))],
        ),
        win32event=event_fake,
    )
    state = build_state(outer_timeout=1.0)
    _answer_with(monkeypatch, b"reply")
    _patch_request(monkeypatch, state)

    with _observability.capture_events() as events:
        _run_worker(state, object())

    _assert_single_completion(events, None)
    [(_handles, _wait_all, wait_ms)] = event_fake.waits
    assert 0 < wait_ms <= 1000, "the write wait did not use the connection budget"


def test_reply_write_timeout_is_reported_as_a_timeout(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A write that outlives the budget times the worker out."""
    file_fake, _pipe_fake = patch_win32(
        FakeWin32File(writes=[ScriptedWrite(status=ERROR_IO_PENDING)]),
        win32event=FakeWin32Event([FakeWin32Event.WAIT_TIMEOUT]),
    )
    state = build_state()
    handle = object()
    _answer_with(monkeypatch, b"reply")
    _patch_request(monkeypatch, state)

    with _observability.capture_events() as events:
        slot = _run_worker(state, handle)

    [timed_out] = _worker_events(events, "timeout")
    assert timed_out.error_category == "Timeout", "wrong reply timeout category"
    _assert_bounded(timed_out)
    assert file_fake.cancelled == [handle], "the pending write was not cancelled"
    assert file_fake.read_calls == 0, "an unconfirmed reply still probed the peer"
    assert closed_pipe_handles(file_fake) == [handle], "the client handle leaked"
    assert slot.released, "the permit was not released"


def test_reply_confirmation_timeout_is_reported_as_a_timeout(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that never closes leaves the reply unconfirmed."""
    file_fake, _pipe_fake = patch_win32(
        FakeWin32File(
            writes=[ScriptedWrite()],
            reads=[ScriptedRead(status=ERROR_IO_PENDING)],
        ),
        win32event=FakeWin32Event([FakeWin32Event.WAIT_TIMEOUT]),
    )
    state = build_state()
    handle = object()
    _answer_with(monkeypatch, b"reply")
    _patch_request(monkeypatch, state)

    with _observability.capture_events() as events:
        slot = _run_worker(state, handle)

    [timed_out] = _worker_events(events, "timeout")
    assert timed_out.error_category == "Timeout", "wrong confirmation category"
    assert file_fake.cancelled == [handle], "the probe read was not cancelled"
    assert file_fake.read_calls == 1, "the probe read never ran"
    assert slot.released, "the permit was not released"


def test_reply_delivery_shutdown_is_reported_as_stopped(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop during delivery completes the worker without a timeout."""
    patch_win32(
        FakeWin32File(writes=[ScriptedWrite(status=ERROR_IO_PENDING)]),
        win32event=FakeWin32Event([FakeWin32Event.WAIT_OBJECT_0 + 1]),
    )
    state = build_state()
    state.stop_event.set()
    _answer_with(monkeypatch, b"reply")
    _patch_request(monkeypatch, state)

    with _observability.capture_events() as events:
        state._serve_one_request(object(), time.perf_counter())

    _assert_single_completion(events, "ServerStopped")
    assert not _worker_events(events, "timeout"), "shutdown was reported as a timeout"


def test_unexpected_client_data_leaves_the_reply_unconfirmed(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data after the reply is a protocol violation, not an acknowledgement."""
    patch_win32(
        FakeWin32File(
            writes=[ScriptedWrite()],
            reads=[ScriptedRead(data=b"x")],
        )
    )
    state = build_state()
    _answer_with(monkeypatch, b"reply")
    _patch_request(monkeypatch, state)

    with _observability.capture_events() as events:
        _run_worker(state, object())

    _assert_single_completion(events, "PipeError")


def test_no_reply_is_written_or_confirmed_without_a_response(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A request that owes no reply performs neither pipe operation."""
    file_fake, _pipe_fake = patch_win32(FakeWin32File())
    state = build_state()
    _answer_with(monkeypatch, None)
    _patch_request(monkeypatch, state)

    with _observability.capture_events() as events:
        _run_worker(state, object())

    _assert_single_completion(events, None)
    assert not file_fake.overlapped_writes, "a reply was written without a response"
    assert not file_fake.read_calls, "the peer was probed without a response"


@pytest.mark.parametrize(
    ("winerror", "category"),
    [
        pytest.param(ERROR_BROKEN_PIPE, "BrokenPipe", id="broken-pipe"),
        pytest.param(ERROR_NO_DATA, "NoData", id="no-data"),
    ],
)
def test_reply_write_disconnect_codes_stay_quiet(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    winerror: int,
    category: str,
) -> None:
    """A client that left before its reply is reported without an error log."""
    caplog.set_level("DEBUG", logger="cmd_mox.ipc.named_pipe")
    patch_win32(FakeWin32File(writes=[ScriptedWrite(error=FakeWinError(winerror))]))
    state = build_state()
    _answer_with(monkeypatch, b"reply")
    _patch_request(monkeypatch, state)

    with _observability.capture_events() as events:
        _run_worker(state, object())

    _assert_single_completion(events, category)
    _assert_no_error_log(caplog)


def test_request_read_and_reply_share_one_deadline(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request read is anchored to the configured connection timeout."""
    patch_win32(
        FakeWin32File(
            writes=[ScriptedWrite()],
            reads=[ScriptedRead(error=FakeWinError(ERROR_BROKEN_PIPE))],
        )
    )
    state = build_state(outer_timeout=2.0)
    _answer_with(monkeypatch, b"reply")
    deadlines = _patch_request(monkeypatch, state)

    started = time.monotonic()
    _run_worker(state, object())
    finished = time.monotonic()

    [deadline] = deadlines
    assert started + 2.0 <= deadline <= finished + 2.0, (
        "the request read did not use the configured per-connection timeout"
    )
