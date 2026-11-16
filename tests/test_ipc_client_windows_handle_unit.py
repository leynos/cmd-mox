"""Unit tests for the Windows IPC client's pipe-handle open and close helpers."""

from __future__ import annotations

import types

import pytest

from cmd_mox.ipc import client, windows
from tests.helpers.ipc_windows import DummyPipeError, patch_windows_modules


@pytest.fixture(autouse=True)
def _patch_windows_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub Windows-only modules so the named-pipe client runs on any platform."""
    patch_windows_modules(monkeypatch)


class _RecordingCreateFile:
    """Fake ``win32file`` recording ``CreateFile`` arguments."""

    GENERIC_READ = 0x8000_0000
    GENERIC_WRITE = 0x4000_0000
    OPEN_EXISTING = 3

    def __init__(self, handle: object, error: BaseException | None = None) -> None:
        """Store the handle to return and an optional failure to raise."""
        self.handle = handle
        self.error = error
        self.calls: list[tuple[object, ...]] = []
        self.closes: list[object] = []

    def CreateFile(self, *args: object) -> object:  # ruff: ignore[invalid-function-name] - mirrors the Windows API casing
        """Record the call and return the handle or raise the configured error."""
        self.calls.append(args)
        if self.error is not None:
            raise self.error
        return self.handle

    def CloseHandle(self, handle: object) -> None:  # ruff: ignore[invalid-function-name] - mirrors the Windows API casing
        """Record each handle close."""
        self.closes += [handle]


class _RecordingPipeState:
    """Fake ``win32pipe`` recording named-pipe mode changes.

    The real module always defines ``PIPE_READMODE_MESSAGE``. Keeping it off
    this base class lets the fallback test prove the literal default is used
    when a pywin32 build omits the constant.
    """

    def __init__(self, error: BaseException | None = None) -> None:
        """Store an optional failure raised by ``SetNamedPipeHandleState``."""
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    def SetNamedPipeHandleState(self, *args: object) -> None:  # ruff: ignore[invalid-function-name] - mirrors the Windows API casing
        """Record the mode-setting call and optionally fail."""
        self.calls.append(args)
        if self.error is not None:
            raise self.error


class _MessageModePipeState(_RecordingPipeState):
    """Recording pipe state exposing a distinctive message-mode constant."""

    PIPE_READMODE_MESSAGE = 0x2D


def test_create_pipe_handle_uses_exact_win32_open_and_mode_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handle is opened read/write in message mode with fixed flags.

    A distinctive constant proves the module attribute is preferred over the
    literal fallback; the fallback itself is covered separately.
    """
    handle = object()
    win32file = _RecordingCreateFile(handle)
    win32pipe = _MessageModePipeState()
    monkeypatch.setattr(client, "win32file", win32file)
    monkeypatch.setattr(client, "win32pipe", win32pipe)

    result = client._create_pipe_handle(r"\\.\pipe\cmd-mox")

    assert result is handle, "the opened handle was not returned"
    expected_create = [
        (
            r"\\.\pipe\cmd-mox",
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )
    ]
    assert win32file.calls == expected_create, (
        "CreateFile did not receive the exact Windows open arguments"
    )
    assert win32pipe.calls == [(handle, 0x2D, None, None)], (
        "the handle did not use the module's message-mode constant"
    )


def test_create_pipe_handle_defaults_message_mode_to_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pywin32 build without the constant still selects message mode."""
    handle = object()
    win32file = _RecordingCreateFile(handle)
    win32pipe = _RecordingPipeState()
    monkeypatch.setattr(client, "win32file", win32file)
    monkeypatch.setattr(client, "win32pipe", win32pipe)

    client._create_pipe_handle("pipe")

    assert win32pipe.calls == [(handle, 2, None, None)], (
        "the read-mode fallback was not the literal message mode"
    )


def test_create_pipe_handle_propagates_the_open_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed ``CreateFile`` surfaces verbatim and skips mode setting."""
    error = DummyPipeError(windows.ERROR_PIPE_BUSY)
    win32file = _RecordingCreateFile(object(), error=error)
    win32pipe = _RecordingPipeState()
    monkeypatch.setattr(client, "win32file", win32file)
    monkeypatch.setattr(client, "win32pipe", win32pipe)

    with pytest.raises(DummyPipeError) as excinfo:
        client._create_pipe_handle("pipe")

    assert excinfo.value is error, "the pipe error was replaced"
    assert not win32pipe.calls, "mode setting ran without a handle"


def test_create_pipe_handle_propagates_the_mode_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed mode change surfaces the original pywin32 error."""
    handle = object()
    error = DummyPipeError(87)
    win32file = _RecordingCreateFile(handle)
    win32pipe = _RecordingPipeState(error=error)
    monkeypatch.setattr(client, "win32file", win32file)
    monkeypatch.setattr(client, "win32pipe", win32pipe)

    with pytest.raises(DummyPipeError) as excinfo:
        client._create_pipe_handle("pipe")

    assert excinfo.value is error, "the mode-setting error was replaced"


def test_pipe_handle_closer_suppresses_pywintypes_errors_and_closes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The close guard ignores pywin32 failures and repeat calls."""
    handle = object()
    closes: list[object] = []

    def close_handle(_handle: object) -> None:
        """Record the close, then fail the way pywin32 may."""
        closes.append(_handle)
        raise DummyPipeError(6)

    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(CloseHandle=close_handle),
    )

    closer = client._pipe_handle_closer(handle)
    closer.close()
    closer.close()

    assert closes == [handle], "the handle close was not attempted exactly once"
