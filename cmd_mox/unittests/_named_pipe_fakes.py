"""Scriptable ``pywin32`` fakes shared by the named-pipe transport tests.

The Windows named-pipe transport cannot run on POSIX, so these fakes stand in
for ``pywintypes``, ``win32event``, ``win32file``, and ``win32pipe``. They are
deliberately scriptable: each fake takes a list of results that it consumes in
order, so a test can describe an exact Windows interaction sequence.

:class:`FakeNamedPipeBackend` implements the transport's
:class:`~cmd_mox.ipc.windows.NamedPipeBackend` port over those fakes. Tests
inject it through :func:`build_state`, so the transport itself never reaches
for a ``pywin32`` module global.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc import named_pipe
from cmd_mox.ipc.windows import NamedPipeBackend, Win32FileProtocol

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from cmd_mox.ipc.windows import PyWinErrorProtocol, PyWinTypesProtocol

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
    start_error:
        Optional error raised from ``ReadFile`` itself, once the buffer has
        been filled. Windows reports a still-pending overlapped read this way,
        so it models ``ERROR_IO_PENDING`` as a raised error rather than a
        returned status.
    """

    data: bytes = b""
    status: int = 0
    more: bool = False
    error: BaseException | None = None
    start_error: BaseException | None = None


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
        BaseException
            The scripted ``start_error``, when one was supplied.
        """  # ruff: ignore[docstring-extraneous-exception] - the scripted start_error is raised verbatim and stays caller-visible
        if not self._reads:
            msg = "no scripted read remains"
            raise AssertionError(msg)
        self._current = self._reads.pop(0)
        size = min(len(self._current.data), len(buffer))
        buffer[:size] = self._current.data[:size]
        if self._current.start_error is not None:
            # The kernel fills the buffer asynchronously, so a pending read
            # still delivers its data even though ``ReadFile`` raised.
            raise self._current.start_error
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


class FakeNamedPipeBackend(NamedPipeBackend):
    """Fake :class:`NamedPipeBackend` delegating to the scriptable modules.

    The transport receives this instead of module globals, so tests describe a
    Windows interaction sequence without mutating ``cmd_mox.ipc.named_pipe``.

    Parameters
    ----------
    win32file:
        Fake ``win32file`` providing reads, writes, and handle bookkeeping.
    win32pipe:
        Fake ``win32pipe`` scripting pipe creation and connection.
    win32event:
        Fake ``win32event`` scripting event creation and waits.
    pywintypes:
        Fake ``pywintypes`` supplying the error type and ``OVERLAPPED``.
    """

    def __init__(
        self,
        win32file: FakeWin32File | None = None,
        win32pipe: FakeWin32Pipe | None = None,
        win32event: FakeWin32Event | None = None,
        pywintypes: FakePyWinTypes | None = None,
    ) -> None:
        self._file_fake = win32file or FakeWin32File()
        self.pipe = win32pipe or FakeWin32Pipe()
        self.event = win32event or FakeWin32Event()
        self._pywintypes_fake = pywintypes or FakePyWinTypes()
        # FakeWinError may omit ``winerror`` to exercise the transport's
        # defensive branch, so it is deliberately wider than the port's error
        # type, which models pywin32's always-coded error.
        self.error = typ.cast("type[PyWinErrorProtocol]", FakeWinError)

        self.INFINITE = FakeWin32Event.INFINITE
        self.WAIT_OBJECT_0 = FakeWin32Event.WAIT_OBJECT_0
        self.WAIT_TIMEOUT = FakeWin32Event.WAIT_TIMEOUT

        self.PIPE_ACCESS_DUPLEX = FakeWin32Pipe.PIPE_ACCESS_DUPLEX
        self.PIPE_TYPE_MESSAGE = FakeWin32Pipe.PIPE_TYPE_MESSAGE
        self.PIPE_READMODE_MESSAGE = FakeWin32Pipe.PIPE_READMODE_MESSAGE
        self.PIPE_WAIT = FakeWin32Pipe.PIPE_WAIT
        self.PIPE_UNLIMITED_INSTANCES = FakeWin32Pipe.PIPE_UNLIMITED_INSTANCES

        self.FILE_FLAG_OVERLAPPED = FakeWin32File.FILE_FLAG_OVERLAPPED
        self.GENERIC_READ = FakeWin32File.GENERIC_READ
        self.GENERIC_WRITE = FakeWin32File.GENERIC_WRITE
        self.OPEN_EXISTING = FakeWin32File.OPEN_EXISTING

    @property
    def win32file(self) -> Win32FileProtocol:
        """The ``win32file`` view the transport hands to its shared helpers.

        The fakes model the whole overlapped-I/O surface, while the helper
        protocol declares only the synchronous subset those helpers call.
        Returning the concrete fake as that narrower view keeps the port
        honest without splitting the fake into two objects.
        """
        return typ.cast("Win32FileProtocol", self._file_fake)

    @property
    def pywintypes(self) -> PyWinTypesProtocol:
        """The ``pywintypes`` view the transport hands to its shared helpers."""
        return typ.cast("PyWinTypesProtocol", self._pywintypes_fake)

    def create_event(self) -> object:
        """Create a fake manual-reset event handle.

        Returns
        -------
        FakeEventHandle
            The newly recorded handle.
        """
        # CreateEvent(security, manual_reset, initial_state, name).
        return self.event.CreateEvent(None, True, False, None)  # ruff: ignore[boolean-positional-value-in-call] - mirrors pywin32's positional flags

    def set_event(self, handle: object) -> None:
        """Record *handle* as signalled."""
        self.event.SetEvent(handle)

    def wait(self, handles: cabc.Sequence[object], timeout_ms: int) -> int:
        """Return the next scripted wait result.

        Returns
        -------
        int
            The scripted ``WaitForMultipleObjects`` result.
        """
        # WaitForMultipleObjects(handles, wait_all, timeout_ms).
        return self.event.WaitForMultipleObjects(list(handles), False, timeout_ms)  # ruff: ignore[boolean-positional-value-in-call] - mirrors pywin32's positional flag

    def create_overlapped(self) -> tuple[object, object]:
        """Build a fake overlapped structure with a dedicated event.

        Returns
        -------
        tuple[FakeOverlapped, FakeEventHandle]
            The overlapped structure and the event attached to it.
        """
        overlapped = FakeOverlapped()
        event = self.create_event()
        overlapped.hEvent = event
        return overlapped, event

    def get_overlapped_result(self, handle: object, overlapped: object) -> int:
        """Complete the scripted overlapped operation.

        Returns
        -------
        int
            The bytes the scripted read transferred, or zero for a connect.
        """
        return self._file_fake.GetOverlappedResult(handle, overlapped, True)  # ruff: ignore[boolean-positional-value-in-call] - mirrors pywin32's positional flag

    def cancel_io(self, handle: object, overlapped: object) -> None:
        """Record the cancellation of *overlapped* on *handle*."""
        self._file_fake.CancelIoEx(handle, overlapped)

    def create_named_pipe(  # ruff: ignore[too-many-positional-arguments,too-many-arguments] - mirrors the port's pywin32-shaped signature
        self,
        name: str,
        open_mode: int,
        pipe_mode: int,
        max_instances: int,
        out_buffer_size: int,
        in_buffer_size: int,
        default_timeout: int,
        security_attributes: object,
    ) -> object:
        """Create the next scripted pipe instance.

        Returns
        -------
        object
            The scripted pipe handle.
        """
        return self.pipe.CreateNamedPipe(
            name,
            open_mode,
            pipe_mode,
            max_instances,
            out_buffer_size,
            in_buffer_size,
            default_timeout,
            security_attributes,
        )

    def connect_named_pipe(self, handle: object, overlapped: object) -> int:
        """Report the next scripted connection status.

        Returns
        -------
        object
            The scripted ``ConnectNamedPipe`` status.
        """
        return typ.cast("int", self.pipe.ConnectNamedPipe(handle, overlapped))

    def disconnect_named_pipe(self, handle: object) -> None:
        """Record the disconnection of *handle*."""
        self.pipe.DisconnectNamedPipe(handle)

    def read_file(
        self, handle: object, buffer: cabc.Buffer, overlapped: object
    ) -> tuple[int, cabc.Buffer]:
        """Start the next scripted overlapped read.

        Returns
        -------
        tuple[int, cabc.Buffer]
            The immediate status and the filled buffer.
        """
        return self._file_fake.ReadFile(
            handle, typ.cast("bytearray", buffer), overlapped
        )

    def allocate_read_buffer(self, size: int) -> cabc.Buffer:
        """Return a writable buffer of *size* bytes.

        Returns
        -------
        bytearray
            A zeroed buffer standing in for a pywin32 read buffer.
        """
        return self._file_fake.AllocateReadBuffer(size)

    def create_file(  # ruff: ignore[too-many-positional-arguments,too-many-arguments] - mirrors the port's pywin32-shaped signature
        self,
        file_name: str,
        desired_access: int,
        share_mode: int,
        security_attributes: object,
        creation_disposition: int,
        flags_and_attributes: int,
        template_file: object,
    ) -> object:
        """Record the wakeup open and return its scripted result.

        Returns
        -------
        object
            The scripted handle, or the scripted error is raised.
        """
        return self._file_fake.CreateFile(
            file_name,
            desired_access,
            share_mode,
            security_attributes,
            creation_disposition,
            flags_and_attributes,
            template_file,
        )

    def close_handle(self, handle: object) -> None:
        """Record *handle* as closed."""
        self._file_fake.CloseHandle(handle)


#: Backend every :func:`build_state` call injects.
#:
#: The object identity is stable so a test may build its state before calling
#: :func:`patch_win32_fixture`; the fixture swaps the inner fakes in place
#: under ``monkeypatch``, which restores them after each test.
_FALLBACK_BACKEND: typ.Final[FakeNamedPipeBackend] = FakeNamedPipeBackend()


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
    """Build the fake backend that :func:`build_state` injects.

    The fakes reach the transport through :class:`FakeNamedPipeBackend` rather
    than module attributes, so this fixture stores the built backend in a
    monkeypatched attribute that :func:`build_state` reads. Tests keep the
    familiar ``patch_win32(win32file=..., win32pipe=..., win32event=...)``
    spelling and the ``(file_fake, pipe_fake)`` return tuple.

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
        # Swap the fakes in place so a state built earlier in the test still
        # observes them; monkeypatch restores the defaults afterwards.
        monkeypatch.setattr(_FALLBACK_BACKEND, "_file_fake", file_fake)
        monkeypatch.setattr(_FALLBACK_BACKEND, "pipe", pipe_fake)
        monkeypatch.setattr(_FALLBACK_BACKEND, "event", win32event or FakeWin32Event())
        monkeypatch.setattr(_FALLBACK_BACKEND, "_pywintypes_fake", FakePyWinTypes())
        return file_fake, pipe_fake

    return _patch


@pytest.fixture(name="windows_platform")
def windows_platform_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend the current platform is Windows for platform-gated code.

    Faking the platform flag on POSIX also has to supply a Win32 backend:
    :class:`~cmd_mox.ipc.named_pipe.NamedPipeServer` constructs the real
    adapter at its transport edge, and that adapter needs ``pywin32``, which
    is absent here. Redirecting the construction site to the fake adapter
    keeps the faked flag and the available backend consistent.
    """
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(named_pipe, "Win32NamedPipeBackend", lambda: _FALLBACK_BACKEND)


def build_state(accept_timeout: float = 0.1) -> named_pipe._NamedPipeState:
    """Create a state object detached from any real IPC server.

    Returns
    -------
    cmd_mox.ipc.named_pipe._NamedPipeState
        A state instance whose outer server is an inert stand-in and whose
        backend is the fake adapter installed by ``patch_win32``.
    """
    return named_pipe._NamedPipeState(
        pipe_name="pipe",
        outer=typ.cast("typ.Any", object()),
        accept_timeout=accept_timeout,
        backend=_FALLBACK_BACKEND,
    )
