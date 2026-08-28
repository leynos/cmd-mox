"""Scriptable ``pywin32`` fakes shared by the named-pipe transport tests.

The Windows named-pipe transport cannot run on POSIX, so these fakes stand in
for ``pywintypes``, ``win32event``, ``win32file``, and ``win32pipe``. They are
deliberately scriptable: each fake takes a list of results that it consumes in
order, so a test can describe an exact Windows interaction sequence.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc import named_pipe

if typ.TYPE_CHECKING:
    import collections.abc as cabc

type Scripted = object | BaseException | cabc.Callable[[], object]

UNEXPECTED_WINERROR: typ.Final[int] = 4321


class FakeWinError(Exception):
    """Stand-in for ``pywintypes.error`` carrying a Windows error code."""

    def __init__(self, winerror: int | None = None) -> None:
        super().__init__(f"fake win32 failure {winerror}")
        if winerror is not None:
            # Omitting the attribute models pywin32 errors lacking a code.
            self.winerror = winerror


class FakeOverlapped:
    """Stand-in for ``pywintypes.OVERLAPPED``."""

    def __init__(self) -> None:
        self.hEvent: object | None = None


class FakePyWinTypes:
    """Fake ``pywintypes`` module exposing the pieces the transport uses."""

    error = FakeWinError
    OVERLAPPED = FakeOverlapped


class FakeEventHandle:
    """Opaque stand-in for a Win32 event handle."""


def resolve_scripted(item: Scripted) -> object:
    """Return the next scripted result, raising or calling it as needed.

    Returns
    -------
    object
        The scripted value, or the return value of a scripted callable.
    """
    if isinstance(item, BaseException):
        raise item
    if callable(item):
        # Scripted callables are side effects such as ``Event.set``.
        return typ.cast("cabc.Callable[[], object]", item)()
    return item


@dc.dataclass(slots=True)
class ScriptedRead:
    """One scripted overlapped read outcome.

    Parameters
    ----------
    data:
        Bytes copied into the caller's buffer.
    status:
        Status ``ReadFile`` reports immediately; ``ERROR_IO_PENDING`` makes the
        transport wait on the overlapped event.
    more:
        Whether ``GetOverlappedResult`` reports ``ERROR_MORE_DATA``.
    error:
        Optional error raised from ``GetOverlappedResult`` instead.
    """

    data: bytes = b""
    status: int = 0
    more: bool = False
    error: BaseException | None = None


class FakeWin32File:
    """Fake ``win32file`` module recording handle and overlapped I/O calls."""

    GENERIC_READ = 0x8000_0000
    GENERIC_WRITE = 0x4000_0000
    FILE_FLAG_OVERLAPPED = 0x4000_0000
    OPEN_EXISTING = 3

    def __init__(
        self,
        create_file_result: Scripted = None,
        reads: list[ScriptedRead] | None = None,
    ) -> None:
        self._create_file_result = create_file_result
        self._reads = list(reads or [])
        self._current: ScriptedRead | None = None
        self.closed: list[object] = []
        self.create_file_calls: list[tuple[object, ...]] = []
        self.cancelled: list[object] = []
        self.writes: list[tuple[object, bytes]] = []

    def CloseHandle(self, handle: object) -> None:  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self.closed.append(handle)

    def CreateFile(self, *args: object) -> object:  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self.create_file_calls.append(args)
        return resolve_scripted(self._create_file_result)

    def WriteFile(self, handle: object, payload: bytes) -> None:  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self.writes.append((handle, payload))

    def FlushFileBuffers(self, handle: object) -> None:  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        """Accept the flush the transport issues after a write."""

    @staticmethod
    def AllocateReadBuffer(size: int) -> bytearray:  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        """Return a writable buffer of *size* bytes.

        Returns
        -------
        bytearray
            A zeroed buffer standing in for a pywin32 read buffer.
        """
        return bytearray(size)

    def ReadFile(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self, handle: object, buffer: bytearray, overlapped: object
    ) -> tuple[int, bytearray]:
        """Start the next scripted overlapped read.

        Returns
        -------
        tuple[int, bytearray]
            The immediate status and the buffer the data was copied into.

        Raises
        ------
        AssertionError
            If the test scripted no further reads.
        """
        if not self._reads:
            msg = "no scripted read remains"
            raise AssertionError(msg)
        self._current = self._reads.pop(0)
        size = min(len(self._current.data), len(buffer))
        buffer[:size] = self._current.data[:size]
        return self._current.status, buffer

    def GetOverlappedResult(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self,
        handle: object,
        overlapped: object,
        wait: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - mirrors pywin32's positional wait flag
    ) -> int:
        """Complete the scripted overlapped read.

        Returns
        -------
        int
            The number of bytes the scripted read transferred, or zero when
            completing an overlapped connect rather than a read.

        Raises
        ------
        FakeWinError
            If the scripted read reports ``ERROR_MORE_DATA`` or another error.
        """
        current = self._current
        if current is None:
            # An overlapped ConnectNamedPipe transfers no bytes.
            return 0
        if current.error is not None:
            raise current.error
        if current.more:
            raise FakeWinError(named_pipe.ERROR_MORE_DATA)
        return len(current.data)

    def CancelIoEx(self, handle: object, overlapped: object) -> None:  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self.cancelled.append(handle)


class FakeWin32Pipe:
    """Fake ``win32pipe`` module scripting creation and connection results."""

    PIPE_ACCESS_DUPLEX = 0x3
    PIPE_TYPE_MESSAGE = 0x4
    PIPE_READMODE_MESSAGE = 0x2
    PIPE_WAIT = 0x0
    PIPE_UNLIMITED_INSTANCES = 255

    def __init__(
        self,
        *,
        handles: list[Scripted] | None = None,
        connect_results: list[Scripted] | None = None,
        disconnect_error: BaseException | None = None,
    ) -> None:
        self._handles = list(handles or [])
        self._connect_results = list(connect_results or [])
        self._disconnect_error = disconnect_error
        self.create_calls: list[tuple[object, ...]] = []
        self.connect_calls: list[object] = []
        self.disconnected: list[object] = []

    def CreateNamedPipe(self, *args: object) -> object:  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self.create_calls.append(args)
        return resolve_scripted(self._handles.pop(0))

    def ConnectNamedPipe(self, handle: object, _overlapped: object) -> object:  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        """Record the connection attempt and report its scripted status.

        Returns
        -------
        object
            The scripted ``ConnectNamedPipe`` status, defaulting to success.
        """
        self.connect_calls.append(handle)
        if self._connect_results:
            return resolve_scripted(self._connect_results.pop(0))
        return 0

    def DisconnectNamedPipe(self, handle: object) -> None:  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self.disconnected.append(handle)
        if self._disconnect_error is not None:
            raise self._disconnect_error


class FakeWin32Event:
    """Fake ``win32event`` module with scriptable wait results."""

    INFINITE = 0xFFFF_FFFF
    WAIT_OBJECT_0 = 0
    WAIT_TIMEOUT = 258

    def __init__(self, wait_results: list[int] | None = None) -> None:
        self._wait_results = list(wait_results or [])
        self.events: list[FakeEventHandle] = []
        self.signalled: list[object] = []
        self.waits: list[tuple[tuple[object, ...], bool, int]] = []

    def CreateEvent(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self,
        _security: object,
        _manual_reset: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - mirrors pywin32's positional flags
        _initial_state: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - mirrors pywin32's positional flags
        _name: object,
    ) -> FakeEventHandle:
        """Create and record a fake event handle.

        Returns
        -------
        FakeEventHandle
            The newly created handle.
        """
        handle = FakeEventHandle()
        self.events.append(handle)
        return handle

    def SetEvent(self, handle: object) -> None:  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self.signalled.append(handle)

    def WaitForMultipleObjects(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self,
        handles: list[object],
        wait_all: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - mirrors pywin32's positional flags
        timeout_ms: int,
    ) -> int:
        """Return the next scripted wait result.

        Returns
        -------
        int
            ``WAIT_OBJECT_0`` unless the test scripted another outcome.
        """
        self.waits.append((tuple(handles), wait_all, timeout_ms))
        if self._wait_results:
            return self._wait_results.pop(0)
        return self.WAIT_OBJECT_0


type PatchWin32 = cabc.Callable[..., tuple[FakeWin32File, FakeWin32Pipe]]


def closed_pipe_handles(file_fake: FakeWin32File) -> list[object]:
    """Return closed handles, excluding the transport's internal event handles.

    Returns
    -------
    list[object]
        Client and wakeup handles the transport closed, in order.
    """
    return [
        handle for handle in file_fake.closed if not isinstance(handle, FakeEventHandle)
    ]


@pytest.fixture(name="patch_win32")
def patch_win32_fixture(monkeypatch: pytest.MonkeyPatch) -> PatchWin32:
    """Install fake ``pywin32`` modules into the named-pipe transport.

    Returns
    -------
    collections.abc.Callable
        Factory installing the fakes and returning the file and pipe fakes.
    """

    def _patch(
        win32file: FakeWin32File | None = None,
        win32pipe: FakeWin32Pipe | None = None,
        win32event: FakeWin32Event | None = None,
    ) -> tuple[FakeWin32File, FakeWin32Pipe]:
        file_fake = win32file or FakeWin32File()
        pipe_fake = win32pipe or FakeWin32Pipe()
        monkeypatch.setattr(named_pipe, "win32file", file_fake)
        monkeypatch.setattr(named_pipe, "win32pipe", pipe_fake)
        monkeypatch.setattr(named_pipe, "win32event", win32event or FakeWin32Event())
        monkeypatch.setattr(named_pipe, "pywintypes", FakePyWinTypes())
        return file_fake, pipe_fake

    return _patch


@pytest.fixture(name="windows_platform")
def windows_platform_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the current platform is Windows for platform-gated code."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)


def build_state(accept_timeout: float = 0.1) -> named_pipe._NamedPipeState:
    """Create a state object detached from any real IPC server.

    Returns
    -------
    cmd_mox.ipc.named_pipe._NamedPipeState
        A state instance whose outer server is an inert stand-in.
    """
    return named_pipe._NamedPipeState(
        pipe_name="pipe",
        outer=typ.cast("typ.Any", object()),
        accept_timeout=accept_timeout,
    )
