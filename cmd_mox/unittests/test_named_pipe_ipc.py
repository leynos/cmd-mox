"""Unit tests for Windows named pipe IPC helpers."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

import cmd_mox.ipc.client as client
import cmd_mox.ipc.server as server
from cmd_mox.ipc import IPCHandlers, NamedPipeServer, Response


class FakeError(Exception):
    """Minimal pywintypes.error replacement for unit tests."""

    def __init__(self, winerror: int) -> None:
        super().__init__(f"error {winerror}")
        self.winerror = winerror


class FakeWin32File:
    """Track writes issued by the client helpers."""

    GENERIC_READ = 1
    GENERIC_WRITE = 2
    OPEN_EXISTING = 3

    def __init__(self) -> None:
        self.written = bytearray()
        self.closed = False

    def WriteFile(self, handle: object, data: bytes) -> None:  # noqa: N802
        """Record bytes written via the fake handle."""
        self.written.extend(data)

    def FlushFileBuffers(self, handle: object) -> None:  # noqa: N802
        """Pretend to flush written data."""

    def CloseHandle(self, handle: object) -> None:  # noqa: N802
        """Mark the fake handle as closed."""
        self.closed = True


class FakeWin32Pipe:
    """Minimal pipe module capturing disconnect calls."""

    ERROR_MORE_DATA = 234
    ERROR_PIPE_CONNECTED = 535
    PIPE_READMODE_MESSAGE = 2

    def __init__(self) -> None:
        self.state_calls: list[tuple[object, int]] = []
        self.disconnected: list[object] = []

    def SetNamedPipeHandleState(  # noqa: N802
        self, handle: object, mode: int, _unused1: object, _unused2: object
    ) -> None:
        """Record state transitions requested by the server."""
        self.state_calls.append((handle, mode))

    def DisconnectNamedPipe(self, handle: object) -> None:  # noqa: N802
        """Track disconnect requests for later assertions."""
        self.disconnected.append(handle)


class FakeNamedPipeHandle:
    """Handle used by :class:`NamedPipeServer` test doubles."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.read_calls = 0
        self.responses: list[bytes] = []


class FakeWin32FileForServer(FakeWin32File):
    """Specialised file API used by :class:`NamedPipeServer` tests."""

    def __init__(self, payload: bytes) -> None:
        super().__init__()
        self.payload = payload

    def ReadFile(  # noqa: N802
        self, handle: FakeNamedPipeHandle, size: int
    ) -> tuple[int, bytes]:
        """Return the payload on first read then simulate a broken pipe."""
        if handle.read_calls:
            raise FakeError(109)
        handle.read_calls += 1
        return 0, handle.payload

    def WriteFile(self, handle: FakeNamedPipeHandle, data: bytes) -> None:  # noqa: N802
        """Collect responses emitted by :class:`NamedPipeServer`."""
        handle.responses.append(data)


@pytest.fixture
def fake_winerror() -> types.SimpleNamespace:
    """Provide a namespace mimicking :mod:`winerror`."""
    return types.SimpleNamespace(
        ERROR_PIPE_BUSY=231,
        ERROR_FILE_NOT_FOUND=2,
        ERROR_BROKEN_PIPE=109,
    )


def test_send_named_pipe_request_writes_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The client helper should write payloads and close handles."""
    fake_file = FakeWin32File()
    fake_pipe = types.SimpleNamespace()
    fake_pywin = types.SimpleNamespace(error=FakeError)
    fake_winerr = types.SimpleNamespace(
        ERROR_PIPE_BUSY=231,
        ERROR_FILE_NOT_FOUND=2,
        ERROR_BROKEN_PIPE=109,
    )

    monkeypatch.setattr(client, "win32file", fake_file)
    monkeypatch.setattr(client, "win32pipe", fake_pipe)
    monkeypatch.setattr(client, "pywintypes", fake_pywin)
    monkeypatch.setattr(client, "winerror", fake_winerr)
    monkeypatch.setattr(client, "_open_pipe_handle", lambda *_: object())
    monkeypatch.setattr(client, "_read_pipe_response", lambda _: b"{}")

    retry = client.RetryConfig(retries=1)
    payload = b"payload"
    result = client._send_named_pipe_request(
        Path("\\\\.\\pipe\\cmdmox-test"), payload, 1.0, retry
    )

    assert fake_file.written == payload
    assert fake_file.closed is True
    assert result == b"{}"


def test_named_pipe_server_handles_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """NamedPipeServer should parse requests and emit handler responses."""
    payload = json.dumps(
        {
            "kind": "invocation",
            "command": "cmd",
            "args": [],
            "stdin": "",
            "env": {},
        }
    ).encode("utf-8")
    fake_handle = FakeNamedPipeHandle(payload)
    fake_pipe = FakeWin32Pipe()
    fake_file = FakeWin32FileForServer(payload)
    fake_pywin = types.SimpleNamespace(error=FakeError)
    fake_winerr = types.SimpleNamespace(
        ERROR_PIPE_BUSY=231,
        ERROR_FILE_NOT_FOUND=2,
        ERROR_BROKEN_PIPE=109,
    )

    def handler(_invocation: server.Invocation) -> Response:
        return Response(stdout="handled")

    named_pipe = NamedPipeServer(
        Path("\\\\.\\pipe\\cmdmox-test"), handlers=IPCHandlers(handler=handler)
    )
    named_pipe._win32pipe = fake_pipe
    named_pipe._win32file = fake_file
    named_pipe._pywintypes = fake_pywin
    named_pipe._winerror = fake_winerr

    named_pipe._handle_connection(fake_handle)

    assert fake_handle.responses
    response_payload = json.loads(fake_handle.responses[0].decode("utf-8"))
    assert response_payload["stdout"] == "handled"
    assert fake_pipe.disconnected == [fake_handle]
    assert fake_file.closed is True
