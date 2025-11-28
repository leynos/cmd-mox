"""Cross-platform unit tests for Windows IPC helpers."""

from __future__ import annotations

import pathlib
import typing as t

import pytest

from cmd_mox.ipc import windows
from cmd_mox.ipc.windows import derive_pipe_name


def test_derive_pipe_name_is_deterministic(tmp_path: pathlib.Path) -> None:
    """Hashing the same identifier should always return the same pipe name."""
    identifier = pathlib.Path(tmp_path) / "shim" / "ipc.sock"
    first = derive_pipe_name(identifier)
    second = derive_pipe_name(identifier)
    assert first == second


def test_derive_pipe_name_varies_per_identifier(tmp_path: pathlib.Path) -> None:
    """Different identifiers should map to different pipe names."""
    first = derive_pipe_name(pathlib.Path(tmp_path) / "one.sock")
    second = derive_pipe_name(pathlib.Path(tmp_path) / "two.sock")
    assert first != second


def test_derive_pipe_name_uses_expected_prefix(tmp_path: pathlib.Path) -> None:
    """Derived pipe names should start with the platform prefix."""
    pipe = derive_pipe_name(pathlib.Path(tmp_path) / "socket")
    assert pipe.startswith(windows.WINDOWS_PIPE_PREFIX)


def test_windows_error_constants_are_positive() -> None:
    """Windows IPC error constants should be positive integers."""
    assert windows.ERROR_PIPE_BUSY > 0
    assert windows.ERROR_FILE_NOT_FOUND > 0


class _FakeWinError(Exception):
    def __init__(self, winerror: int) -> None:
        super().__init__(f"fake winerror {winerror}")
        self.winerror = winerror


class _FakeWin32File:
    def __init__(self, responses: list[t.Any]) -> None:
        self._responses = list(responses)
        self.read_sizes: list[int] = []
        self.writes: list[tuple[object, bytes]] = []
        self.flush_calls = 0

    def ReadFile(  # noqa: N802 - mirror pywin32 API casing for realism
        self, handle: object, chunk_size: int
    ) -> tuple[int, bytes]:
        self.read_sizes.append(chunk_size)
        if not self._responses:
            msg = "No response configured for ReadFile call"
            raise AssertionError(msg)
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return t.cast("tuple[int, bytes]", response)

    def WriteFile(  # noqa: N802 - mirror pywin32 API casing for realism
        self, handle: object, payload: bytes
    ) -> None:
        self.writes.append((handle, payload))

    def FlushFileBuffers(  # noqa: N802 - mirror pywin32 API casing for realism
        self, handle: object
    ) -> None:
        self.flush_calls += 1


class _FakePyWinTypes:
    error = _FakeWinError


def test_read_pipe_message_collects_chunks_until_complete() -> None:
    """Chunked reads should concatenate data until completion marker."""
    win32file = _FakeWin32File(
        [
            (windows.ERROR_MORE_DATA, b"hello "),
            (0, b"world"),
        ]
    )

    payload = windows.read_pipe_message(
        object(),
        win32file=win32file,
        pywintypes=_FakePyWinTypes,
    )

    assert payload == b"hello world"
    assert win32file.read_sizes == [windows.PIPE_CHUNK_SIZE, windows.PIPE_CHUNK_SIZE]


def test_read_pipe_message_returns_partial_on_broken_pipe() -> None:
    """Broken pipes should return any data received before disconnect."""
    win32file = _FakeWin32File(
        [
            (windows.ERROR_MORE_DATA, b"partial"),
            _FakeWinError(windows.ERROR_BROKEN_PIPE),
        ]
    )

    payload = windows.read_pipe_message(
        object(),
        win32file=win32file,
        pywintypes=_FakePyWinTypes,
    )

    assert payload == b"partial"


def test_read_pipe_message_raises_unexpected_errors() -> None:
    """Unexpected Windows errors should propagate to callers."""
    win32file = _FakeWin32File([_FakeWinError(windows.ERROR_FILE_NOT_FOUND)])

    with pytest.raises(_FakeWinError):
        windows.read_pipe_message(
            object(),
            win32file=win32file,
            pywintypes=_FakePyWinTypes,
        )


def test_write_pipe_payload_flushes_buffer() -> None:
    """Writes should flush so readers receive the message immediately."""
    win32file = _FakeWin32File([])
    handle = object()

    windows.write_pipe_payload(
        handle,
        b"payload-bytes",
        win32file=win32file,
    )

    assert win32file.writes == [(handle, b"payload-bytes")]
    assert win32file.flush_calls == 1
