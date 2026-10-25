"""Unit and behavioural tests for shared Windows pipe helpers."""

from __future__ import annotations

import threading
import types
import typing as typ
from pathlib import Path

import hypothesis.strategies as st
import pytest
from hypothesis import given

from cmd_mox.ipc import client
from cmd_mox.ipc._server_core import _RequestResult
from cmd_mox.ipc.client import RetryConfig, _ConnectionContext, _send_pipe_request
from cmd_mox.ipc.named_pipe import _NamedPipeState
from cmd_mox.ipc.windows import (
    ERROR_BROKEN_PIPE,
    ERROR_MORE_DATA,
    MAX_MESSAGE_SIZE,
    PIPE_CHUNK_SIZE,
    PipeMessageTooLargeError,
    PipeReadOptions,
    Win32FileProtocol,
    _read_pipe_chunk,
    read_pipe_message,
)
from cmd_mox.unittests._named_pipe_fakes import (
    FakeNamedPipeBackend,
    FakeWin32File,
    FakeWinError,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc


class _UnusedError(Exception):
    """Placeholder ``pywintypes.error`` for readers that never raise."""


class _FakeWin32File(Win32FileProtocol):
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.writes: list[tuple[object, bytes]] = []
        self.flushes: list[object] = []
        self.closes: list[object] = []

    def ReadFile(self, handle: object, chunk_size: int) -> tuple[int, bytes]:  # ruff: ignore[invalid-function-name] - the protocol mirrors the external Windows API casing
        if not self.responses:
            msg = "No response configured"
            raise AssertionError(msg)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value, ty:invalid-return-type]

    def WriteFile(self, handle: object, payload: bytes) -> None:  # ruff: ignore[invalid-function-name] - the protocol mirrors the external Windows API casing
        self.writes.append((handle, payload))

    def FlushFileBuffers(self, handle: object) -> None:  # ruff: ignore[invalid-function-name] - the protocol mirrors the external Windows API casing
        self.flushes.append(handle)

    def CloseHandle(self, handle: object) -> None:  # ruff: ignore[invalid-function-name] - the protocol mirrors the external Windows API casing
        self.closes.append(handle)


def test_read_pipe_message_logs_unexpected_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unexpected status codes should be logged and return partial data."""
    caplog.set_level("WARNING")
    win32file = _FakeWin32File([(999, b"partial")])

    payload = read_pipe_message(
        object(),
        win32file=win32file,
        pywintypes=types.SimpleNamespace(error=Exception),
    )

    assert payload == b"partial"
    assert any("Unexpected ReadFile status" in rec.message for rec in caplog.records)


def test_send_pipe_request_uses_shared_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Client request path should delegate to shared pipe helpers."""
    writes: list[tuple[object, bytes, object]] = []
    handle = object()

    def fake_write(h: object, payload: bytes, *, win32file: object) -> None:
        writes.append((h, payload, win32file))

    def fake_read(
        h: object, *, win32file: object, pywintypes: object, options: object
    ) -> bytes:
        return b"response"

    monkeypatch.setattr("cmd_mox.ipc.client.write_pipe_payload", fake_write)
    monkeypatch.setattr("cmd_mox.ipc.client.read_pipe_message", fake_read)
    monkeypatch.setattr(
        "cmd_mox.ipc.client._connect_pipe_with_retries",
        lambda *args, **kwargs: handle,
    )
    monkeypatch.setattr(
        "cmd_mox.ipc.client.pywintypes", types.SimpleNamespace(error=Exception)
    )

    result = _send_pipe_request(
        Path("socket"),
        b"payload",
        _ConnectionContext(timeout=1.0, retry_config=RetryConfig()),
    )

    assert result == b"response"
    assert writes
    assert writes[0][0] is handle
    assert writes[0][1] == b"payload"


def test_named_pipe_handler_uses_shared_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server handler delegates to read/write helpers for pipe I/O."""
    writes: list[bytes] = []
    read_calls: list[object] = []
    handle = object()

    def fake_read(handle_obj: object, **_kwargs: object) -> bytes:
        read_calls.append(handle_obj)
        return b"raw-request"

    def fake_write(handle_obj: object, payload: bytes, *, win32file: object) -> None:
        writes.append(payload)

    def fake_process(_outer: object, raw: bytes, transport: str) -> _RequestResult:
        """Return a completed response for the named-pipe handler test.

        Returns
        -------
        _RequestResult
            A response without a correlation identifier.
        """
        assert raw == b"raw-request"
        assert transport == "named_pipe"
        return _RequestResult(response_bytes=b"processed", correlation_id=None)

    file_fake = FakeWin32File()
    state = _NamedPipeState(
        pipe_name="pipe",
        outer=object(),  # type: ignore[arg-type, ty:invalid-argument-type]
        accept_timeout=0.1,
        backend=FakeNamedPipeBackend(win32file=file_fake),
    )
    state._client_threads.add(threading.current_thread())

    monkeypatch.setattr("cmd_mox.ipc.named_pipe.read_pipe_message", fake_read)
    monkeypatch.setattr("cmd_mox.ipc.named_pipe.write_pipe_payload", fake_write)
    monkeypatch.setattr("cmd_mox.ipc.named_pipe._request_pipeline", fake_process)

    state._handle_client(handle)

    assert read_calls == [handle]
    assert writes == [b"processed"]
    assert file_fake.closed == [handle], "the client handle was not closed"


def test_named_pipe_server_signals_ready_when_accept_creation_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Pipe creation failures should signal readiness and stop accepting clients."""

    def fail_pipe_creation() -> object:
        raise FakeWinError(ERROR_BROKEN_PIPE)

    backend = FakeNamedPipeBackend()
    state = _NamedPipeState(
        pipe_name="pipe",
        outer=object(),  # type: ignore[arg-type, ty:invalid-argument-type]
        accept_timeout=0.1,
        backend=backend,
    )
    caplog.set_level("ERROR", logger="cmd_mox.ipc.named_pipe")
    monkeypatch.setattr("cmd_mox.ipc.named_pipe.path_utils.IS_WINDOWS", True)
    monkeypatch.setattr(state, "_create_pipe_instance", fail_pipe_creation)

    state.serve_forever()

    assert state.ready_event.is_set(), "Pipe creation failure did not signal readiness"
    assert "Named pipe accept failed" in caplog.text, (
        "Pipe creation failure was not logged with the accept-loop context"
    )


@pytest.mark.parametrize(
    ("chunks", "max_bytes", "expected"),
    [
        ([b"abcd", b"efgh"], 8, b"abcdefgh"),
        ([b"abcd", b"efg"], 8, b"abcdefg"),
        ([b"a"], 1, b"a"),
    ],
    ids=["exactly-at-limit", "below-limit", "single-byte-limit"],
)
def test_read_pipe_message_accepts_messages_within_the_limit(
    chunks: list[bytes], max_bytes: int, expected: bytes
) -> None:
    """A message at or below the limit is returned in full."""
    responses: list[object] = [(ERROR_MORE_DATA, chunk) for chunk in chunks[:-1]]
    responses.append((0, chunks[-1]))
    win32file = _FakeWin32File(responses)

    payload = read_pipe_message(
        object(),
        win32file=win32file,
        pywintypes=types.SimpleNamespace(error=_UnusedError),
        options=PipeReadOptions(chunk_size=4, max_bytes=max_bytes),
    )

    assert payload == expected, "bounded read altered an in-limit message"


def test_read_pipe_message_rejects_oversized_multi_chunk_message() -> None:
    """A multi-chunk message beyond the limit is refused, not buffered."""
    win32file = _FakeWin32File([
        (ERROR_MORE_DATA, b"abcd"),
        (ERROR_MORE_DATA, b"efgh"),
        (0, b"ijkl"),
    ])

    with pytest.raises(PipeMessageTooLargeError) as excinfo:
        read_pipe_message(
            object(),
            win32file=win32file,
            pywintypes=types.SimpleNamespace(error=_UnusedError),
            options=PipeReadOptions(chunk_size=4, max_bytes=8),
        )

    assert excinfo.value.received == 12, "byte count not reported"
    assert excinfo.value.limit == 8, "limit not reported"
    assert not win32file.responses, "reader stopped before the final chunk"
    assert "abcd" not in str(excinfo.value), "message data leaked into the error"


class _FakeWinError(Exception):
    """Minimal ``pywintypes.error`` double carrying a Windows error code."""

    def __init__(self, winerror: int) -> None:
        super().__init__(winerror)
        self.winerror = winerror


def _scripted_read_chunk(
    steps: list[tuple[bytes, int]], *, broken_pipe_at: int | None = None
) -> cabc.Callable[[int], tuple[int, bytes]]:
    """Build a reader that consumes scripted outcomes in order."""  # ruff: ignore[docstring-missing-returns] - private test reader has an obvious callable return
    pending_steps = list(steps)
    read_count = 0

    def read_chunk(_requested_size: int) -> tuple[int, bytes]:
        nonlocal read_count
        if broken_pipe_at == read_count:
            raise _FakeWinError(ERROR_BROKEN_PIPE)
        if not pending_steps:
            msg = "scripted reader ran out of steps before a terminal status"
            raise AssertionError(msg)
        data, status = pending_steps.pop(0)
        read_count += 1
        return status, data

    return read_chunk


def _scripted_message_payload(steps: list[tuple[bytes, int]]) -> bytes:
    """Return scripted bytes through the first status that ends the read."""  # ruff: ignore[docstring-missing-returns] - private test oracle has an obvious bytes return
    terminal_index = next(
        index
        for index, (_data, status) in enumerate(steps)
        if status != ERROR_MORE_DATA
    )
    return b"".join(data for data, _status in steps[: terminal_index + 1])


def _read_scripted_message(
    steps: list[tuple[bytes, int]],
    *,
    chunk_size: int,
    max_bytes: int,
    broken_pipe_at: int | None = None,
) -> bytes:
    """Read a scripted message through the injected reader."""  # ruff: ignore[docstring-missing-returns] - private wrapper has an obvious bytes return
    return read_pipe_message(
        object(),
        # The injected reader means this value is never dereferenced.
        win32file=typ.cast("Win32FileProtocol", None),
        pywintypes=types.SimpleNamespace(error=_FakeWinError),
        options=PipeReadOptions(
            chunk_size=chunk_size,
            max_bytes=max_bytes,
            read_chunk=_scripted_read_chunk(steps, broken_pipe_at=broken_pipe_at),
        ),
    )


#: Statuses returned by a scripted read; only ERROR_MORE_DATA continues.
_UNEXPECTED_READ_STATUS = 1234
_READ_STATUSES = st.sampled_from((0, ERROR_MORE_DATA, _UNEXPECTED_READ_STATUS))
_TERMINAL_READ_STATUSES = st.sampled_from((0, _UNEXPECTED_READ_STATUS))

#: Bounded read scripts always end, while earlier statuses can terminate early.
_READ_SCRIPT_CASES = st.integers(min_value=1, max_value=8).flatmap(
    lambda chunk_size: st.tuples(
        st.lists(st.tuples(st.binary(max_size=chunk_size), _READ_STATUSES), max_size=4),
        st.tuples(st.binary(max_size=chunk_size), _TERMINAL_READ_STATUSES),
    ).map(
        lambda outcomes: (
            chunk_size,
            [*outcomes[0], outcomes[1]],
        )
    )
)

#: Successful cases choose a limit large enough for the first delivered prefix.
_BOUNDED_READ_CASES = _READ_SCRIPT_CASES.flatmap(
    lambda case: st.tuples(
        st.just(case[0]),
        st.just(case[1]),
        st.integers(
            min_value=max(1, len(_scripted_message_payload(case[1]))),
            max_value=40,
        ),
    )
)

#: Oversized reads combine individually bounded chunks past the total limit.
_OVERSIZED_READ_CASES = st.integers(min_value=1, max_value=8).flatmap(
    lambda max_bytes: st.integers(min_value=1, max_value=max_bytes).flatmap(
        lambda first_size: st.integers(
            min_value=max_bytes - first_size + 1,
            max_value=max_bytes,
        ).flatmap(
            lambda second_size: st.tuples(
                st.just(max_bytes),
                st.binary(min_size=first_size, max_size=first_size),
                st.binary(min_size=second_size, max_size=second_size),
                _TERMINAL_READ_STATUSES,
                st.integers(
                    min_value=max(first_size, second_size),
                    max_value=8,
                ),
            )
        )
    )
)

#: Broken-pipe cases stop after a received chunk and before a later one.
_BROKEN_PIPE_CASES = st.integers(min_value=1, max_value=8).flatmap(
    lambda chunk_size: st.lists(
        st.binary(min_size=1, max_size=chunk_size), min_size=2, max_size=6
    ).flatmap(
        lambda chunks: st.integers(min_value=1, max_value=len(chunks) - 1).flatmap(
            lambda broken_pipe_at: st.tuples(
                st.just(chunk_size),
                st.just(chunks),
                st.just(broken_pipe_at),
                st.integers(
                    min_value=sum(map(len, chunks[:broken_pipe_at])),
                    max_value=40,
                ),
            )
        )
    )
)


def test_read_pipe_chunk_reports_peer_disconnection() -> None:
    """A broken pipe ends the read rather than propagating."""

    def reader(_size: int) -> tuple[int, bytes]:
        raise _FakeWinError(ERROR_BROKEN_PIPE)

    result = _read_pipe_chunk(
        reader, 4, pywintypes=types.SimpleNamespace(error=_FakeWinError)
    )

    assert result is None, "a broken pipe should signal completion"


def test_read_pipe_chunk_propagates_unexpected_errors() -> None:
    """Any other Windows error reaches the caller unchanged."""
    unexpected = 1234

    def reader(_size: int) -> tuple[int, bytes]:
        raise _FakeWinError(unexpected)

    with pytest.raises(_FakeWinError) as excinfo:
        _read_pipe_chunk(
            reader, 4, pywintypes=types.SimpleNamespace(error=_FakeWinError)
        )

    assert excinfo.value.winerror == unexpected, "wrong error propagated"


def test_read_pipe_message_uses_the_injected_reader() -> None:
    """A supplied reader replaces the synchronous ``ReadFile`` call."""
    seen: list[int] = []

    def reader(size: int) -> tuple[int, bytes]:
        seen.append(size)
        return 0, b"payload"

    payload = read_pipe_message(
        object(),
        win32file=_FakeWin32File([]),
        pywintypes=types.SimpleNamespace(error=_UnusedError),
        options=PipeReadOptions(chunk_size=17, read_chunk=reader),
    )

    assert payload == b"payload", "injected reader was not used"
    assert seen == [17], "chunk size was not forwarded to the reader"


@given(case=_BOUNDED_READ_CASES)
def test_read_pipe_message_never_returns_more_than_its_limit(
    case: tuple[int, list[tuple[bytes, int]], int],
) -> None:
    """Invariant: every successful read fits its configured byte limit."""
    chunk_size, steps, max_bytes = case

    payload = _read_scripted_message(steps, chunk_size=chunk_size, max_bytes=max_bytes)

    assert len(payload) <= max_bytes, "successful read exceeded its byte limit"


@given(case=_OVERSIZED_READ_CASES)
def test_read_pipe_message_rejects_oversized_generated_input(
    case: tuple[int, bytes, bytes, int, int],
) -> None:
    """Invariant: oversized reads raise without retaining or exposing data."""
    max_bytes, first_chunk, second_chunk, status, chunk_size = case
    steps = [(first_chunk, ERROR_MORE_DATA), (second_chunk, status)]

    assert len(first_chunk) <= max_bytes, "first chunk exceeded the byte limit"
    assert len(second_chunk) <= max_bytes, "second chunk exceeded the byte limit"

    with pytest.raises(PipeMessageTooLargeError) as excinfo:
        _read_scripted_message(steps, chunk_size=chunk_size, max_bytes=max_bytes)

    error = excinfo.value
    received = len(first_chunk) + len(second_chunk)
    assert vars(error) == {"received": received, "limit": max_bytes}, (
        "oversized-read error fields did not match received bytes and limit"
    )
    assert type(error.received) is int, "error received count was not an integer"
    assert type(error.limit) is int, "error byte limit was not an integer"
    expected_message = f"named pipe message exceeded the {max_bytes}-byte limit"
    assert error.args == (expected_message,), "exception arguments exposed message data"
    assert str(error) == expected_message, "exception text exposed message data"


@given(case=_BOUNDED_READ_CASES)
def test_read_pipe_message_concatenates_through_the_first_terminal_status(
    case: tuple[int, list[tuple[bytes, int]], int],
) -> None:
    """Invariant: returned bytes stop at the first non-continuation status."""
    chunk_size, steps, max_bytes = case

    payload = _read_scripted_message(steps, chunk_size=chunk_size, max_bytes=max_bytes)

    assert payload == _scripted_message_payload(steps), (
        "returned payload did not stop at the first terminal status"
    )


@given(case=_BROKEN_PIPE_CASES)
def test_read_pipe_message_returns_prefix_on_generated_broken_pipe(
    case: tuple[int, list[bytes], int, int],
) -> None:
    """Invariant: a mid-sequence broken pipe returns exactly its read prefix."""
    chunk_size, chunks, broken_pipe_at, max_bytes = case
    steps = [(chunk, ERROR_MORE_DATA) for chunk in chunks]

    payload = _read_scripted_message(
        steps,
        chunk_size=chunk_size,
        max_bytes=max_bytes,
        broken_pipe_at=broken_pipe_at,
    )

    assert payload == b"".join(chunks[:broken_pipe_at]), (
        "broken pipe did not return exactly the received prefix"
    )


@pytest.mark.parametrize(
    ("chunk_size", "max_bytes", "field"),
    [
        pytest.param(0, MAX_MESSAGE_SIZE, "chunk_size", id="zero-chunk-size"),
        pytest.param(-1, MAX_MESSAGE_SIZE, "chunk_size", id="negative-chunk-size"),
        pytest.param(PIPE_CHUNK_SIZE, 0, "max_bytes", id="zero-max-bytes"),
        pytest.param(PIPE_CHUNK_SIZE, -1, "max_bytes", id="negative-max-bytes"),
    ],
)
def test_pipe_read_options_reject_non_positive_bounds(
    chunk_size: int, max_bytes: int, field: str
) -> None:
    """Non-positive read tunables are refused at construction."""
    with pytest.raises(ValueError, match=f"{field} must be positive"):
        PipeReadOptions(chunk_size=chunk_size, max_bytes=max_bytes)


def test_join_with_timeout_waits_for_the_cancelled_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The timeout is not reported until the cancelled worker has exited."""
    monkeypatch.setattr(client, "IO_CANCEL_GRACE", 1.0)
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()

    try:
        with pytest.raises(TimeoutError) as excinfo:
            client._join_with_timeout_and_cancel(worker, 0.01, release.set)
    finally:
        release.set()
        worker.join(1.0)

    assert "did not exit" not in str(excinfo.value), "worker exit was not awaited"
    assert not worker.is_alive(), "the worker was still running on return"


def test_join_with_timeout_reports_a_wedged_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker that ignores cancellation is named in the raised error."""
    monkeypatch.setattr(client, "IO_CANCEL_GRACE", 0.01)
    release = threading.Event()
    worker = threading.Thread(target=release.wait, daemon=True)
    worker.start()

    try:
        with pytest.raises(TimeoutError, match="did not exit after cancellation"):
            client._join_with_timeout_and_cancel(worker, 0.01, lambda: None)
    finally:
        release.set()
        worker.join(1.0)


def test_synchronous_io_cancel_is_skipped_without_pywin32() -> None:
    """The cancellation request is inert when the Windows API is absent."""
    worker = threading.Thread(target=lambda: None, daemon=True)
    worker.start()
    worker.join()

    client._request_synchronous_io_cancel(worker)


def test_synchronous_io_cancel_opens_and_closes_the_thread_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When exposed, ``CancelSynchronousIo`` runs against the worker's handle."""
    handle = object()
    cancelled: list[object] = []
    closed: list[object] = []
    opened: list[tuple[object, ...]] = []

    monkeypatch.setattr(
        client,
        "win32api",
        types.SimpleNamespace(
            OpenThread=lambda *args: (opened.append(args), handle)[1]
        ),
    )
    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(
            CancelSynchronousIo=cancelled.append,
            CloseHandle=closed.append,
        ),
    )
    # A Windows host always has pywintypes alongside win32file; stub it too so
    # the handle guard can resolve the error type it suppresses.
    monkeypatch.setattr(
        client, "pywintypes", types.SimpleNamespace(error=_FakeWinError)
    )
    worker = threading.Thread(target=lambda: None, daemon=True)
    worker.start()
    worker.join()

    client._request_synchronous_io_cancel(worker)

    assert cancelled == [handle], "the worker's I/O was not cancelled"
    assert closed == [handle], "the thread handle was not closed"
    assert len(opened) == 1, "the thread handle was not opened once"
    assert opened[0][2] == worker.native_id, "wrong thread targeted"
