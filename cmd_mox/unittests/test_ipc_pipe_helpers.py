"""Unit and behavioural tests for shared Windows pipe helpers."""

from __future__ import annotations

import threading
import types
from pathlib import Path

import pytest

from cmd_mox.ipc.client import RetryConfig, _ConnectionContext, _send_pipe_request
from cmd_mox.ipc.named_pipe import _NamedPipeState
from cmd_mox.ipc.windows import (
    ERROR_MORE_DATA,
    PipeMessageTooLargeError,
    PipeReadOptions,
    Win32FileProtocol,
    read_pipe_message,
)


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
    closes: list[object] = []
    read_calls: list[object] = []
    handle = object()

    def fake_read(handle_obj: object, **_kwargs: object) -> bytes:
        read_calls.append(handle_obj)
        return b"raw-request"

    def fake_write(handle_obj: object, payload: bytes, *, win32file: object) -> None:
        writes.append(payload)

    def fake_process(_outer: object, raw: bytes, transport: str) -> bytes:
        assert raw == b"raw-request"
        assert transport == "named_pipe"
        return b"processed"

    class _FakeWin32File:
        def CloseHandle(self, h: object) -> None:  # ruff: ignore[invalid-function-name] - the protocol mirrors the external Windows API casing
            closes.append(h)

    class _FakeWin32Pipe:
        @staticmethod
        def DisconnectNamedPipe(_handle: object) -> None:  # ruff: ignore[invalid-function-name] - the protocol mirrors the external Windows API casing
            return None

    class _FakePyWinTypes:
        error = type("Err", (Exception,), {})

    dummy_outer = object()
    state = _NamedPipeState(
        pipe_name="pipe",
        outer=dummy_outer,  # type: ignore[arg-type, ty:invalid-argument-type]
        accept_timeout=0.1,
    )
    state._client_threads.add(threading.current_thread())

    monkeypatch.setattr("cmd_mox.ipc.named_pipe.read_pipe_message", fake_read)
    monkeypatch.setattr("cmd_mox.ipc.named_pipe.write_pipe_payload", fake_write)
    monkeypatch.setattr("cmd_mox.ipc.named_pipe._request_pipeline", fake_process)
    monkeypatch.setattr("cmd_mox.ipc.named_pipe.win32file", _FakeWin32File())
    monkeypatch.setattr("cmd_mox.ipc.named_pipe.win32pipe", _FakeWin32Pipe())
    monkeypatch.setattr("cmd_mox.ipc.named_pipe.pywintypes", _FakePyWinTypes())

    state._handle_client(handle)

    assert read_calls == [handle]
    assert writes == [b"processed"]
    assert closes == [handle]


def test_named_pipe_server_signals_ready_when_accept_creation_fails(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Pipe creation failures should signal readiness and stop accepting clients."""

    class _FakePipeError(Exception):
        pass

    def fail_pipe_creation() -> object:
        msg = "pipe creation failed"
        raise _FakePipeError(msg)

    state = _NamedPipeState(
        pipe_name="pipe",
        outer=object(),  # type: ignore[arg-type, ty:invalid-argument-type]
        accept_timeout=0.1,
    )
    caplog.set_level("ERROR", logger="cmd_mox.ipc.named_pipe")
    monkeypatch.setattr("cmd_mox.ipc.named_pipe.path_utils.IS_WINDOWS", True)
    monkeypatch.setattr(
        "cmd_mox.ipc.named_pipe.pywintypes",
        types.SimpleNamespace(error=_FakePipeError),
    )
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
