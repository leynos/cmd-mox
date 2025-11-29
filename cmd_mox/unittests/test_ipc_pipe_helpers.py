"""Unit and behavioural tests for shared Windows pipe helpers."""

from __future__ import annotations

import threading
import types
import typing as t
from pathlib import Path

from cmd_mox.ipc.client import RetryConfig, _send_pipe_request
from cmd_mox.ipc.server import _NamedPipeState
from cmd_mox.ipc.windows import Win32FileProtocol, read_pipe_message

if t.TYPE_CHECKING:
    import pytest


class _FakeWin32File(Win32FileProtocol):
    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.writes: list[tuple[object, bytes]] = []
        self.flushes: list[object] = []
        self.closes: list[object] = []

    def ReadFile(self, handle: object, chunk_size: int) -> tuple[int, bytes]:  # noqa: N802
        if not self.responses:
            msg = "No response configured"
            raise AssertionError(msg)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response  # type: ignore[return-value]

    def WriteFile(self, handle: object, payload: bytes) -> None:  # noqa: N802
        self.writes.append((handle, payload))

    def FlushFileBuffers(self, handle: object) -> None:  # noqa: N802
        self.flushes.append(handle)

    def CloseHandle(self, handle: object) -> None:  # noqa: N802 - helper
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


def test_send_pipe_request_uses_shared_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Client request path should delegate to shared pipe helpers."""
    writes: list[tuple[object, bytes, object]] = []
    handle = object()

    def fake_write(h: object, payload: bytes, *, win32file: object) -> None:
        writes.append((h, payload, win32file))

    def fake_read(
        h: object, *, win32file: object, pywintypes: object, chunk_size: int
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
        timeout=1.0,
        retry_config=RetryConfig(),
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

    def fake_process(_outer: object, raw: bytes) -> bytes:
        assert raw == b"raw-request"
        return b"processed"

    class _FakeWin32File:
        def CloseHandle(self, h: object) -> None:  # noqa: N802
            closes.append(h)

    class _FakeWin32Pipe:
        @staticmethod
        def DisconnectNamedPipe(_handle: object) -> None:  # noqa: N802
            return None

    class _FakePyWinTypes:
        error = type("Err", (Exception,), {})

    dummy_outer = object()
    state = _NamedPipeState(
        pipe_name="pipe",
        outer=dummy_outer,  # type: ignore[arg-type]
        accept_timeout=0.1,
    )
    state._client_threads.add(threading.current_thread())

    monkeypatch.setattr("cmd_mox.ipc.server.read_pipe_message", fake_read)
    monkeypatch.setattr("cmd_mox.ipc.server.write_pipe_payload", fake_write)
    monkeypatch.setattr("cmd_mox.ipc.server._process_raw_request", fake_process)
    monkeypatch.setattr("cmd_mox.ipc.server.win32file", _FakeWin32File())
    monkeypatch.setattr("cmd_mox.ipc.server.win32pipe", _FakeWin32Pipe())
    monkeypatch.setattr("cmd_mox.ipc.server.pywintypes", _FakePyWinTypes())

    state._handle_client(handle)

    assert read_calls == [handle]
    assert writes == [b"processed"]
    assert closes == [handle]
