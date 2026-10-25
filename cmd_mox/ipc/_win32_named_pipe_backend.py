"""Win32 adapter for the named-pipe transport's :class:`NamedPipeBackend` port.

This module is the transport's single point of contact with ``pywin32``. The
guarded module bindings live here, and every transport call goes through
:class:`Win32NamedPipeBackend` rather than reaching for a module global, so the
rest of the transport runs unchanged against a fake adapter on any platform.
"""

from __future__ import annotations

import importlib
import typing as typ

from cmd_mox import _path_utils as path_utils

from .windows import (
    NamedPipeBackend,
    NamedPipeCreateSpec,
    PipeFileOpenSpec,
    PyWinErrorProtocol,
    PyWinTypesProtocol,
    Win32FileProtocol,
)

if typ.TYPE_CHECKING:  # pragma: no cover - type-checkers only
    import collections.abc as cabc

    # Annotated rather than inferred: the Windows branch rebinds these to real
    # modules and the fallback branch to ``None``, which type-checkers cannot
    # reconcile without help.
    pywintypes: typ.Any
    win32event: typ.Any
    win32file: typ.Any
    win32pipe: typ.Any

if path_utils.IS_WINDOWS:  # pragma: win32-only
    try:
        pywintypes = importlib.import_module("pywintypes")
        win32event = importlib.import_module("win32event")
        win32file = importlib.import_module("win32file")
        win32pipe = importlib.import_module("win32pipe")
    except ModuleNotFoundError as exc:  # pragma: no cover - import guard
        msg = "pywin32 is required for Windows named pipe support"
        raise RuntimeError(msg) from exc
else:  # pragma: no cover - non-Windows fallback for type-checkers
    pywintypes = typ.cast("typ.Any", None)
    win32event = typ.cast("typ.Any", None)
    win32file = typ.cast("typ.Any", None)
    win32pipe = typ.cast("typ.Any", None)


class Win32NamedPipeBackend(NamedPipeBackend):
    """Real :class:`NamedPipeBackend`, backed by the imported ``pywin32`` modules.

    Construction reads the guarded module bindings, not the class body, because
    those bindings are ``None`` off Windows: the transport is never started
    there, but this module still has to import so the rest of the package loads.

    Each method forwards one port operation to pywin32 and nothing else. That
    one-to-one correspondence is the point: it keeps every Win32 call site
    visible in a single file and lets a fake adapter stand in for this one.
    """

    def __init__(self) -> None:
        # Hold the modules on the instance: every operation below reads them
        # through ``self``, which makes the adapter's whole Win32 dependency
        # visible in one place instead of hidden behind module-global lookups.
        self._pywintypes = pywintypes
        self._win32event = win32event
        self._win32file = win32file
        self._win32pipe = win32pipe

        self.win32file: Win32FileProtocol = win32file
        self.pywintypes: PyWinTypesProtocol = pywintypes
        self.error: type[PyWinErrorProtocol] = pywintypes.error

        self.INFINITE: int = self._win32event.INFINITE
        self.WAIT_OBJECT_0: int = self._win32event.WAIT_OBJECT_0
        self.WAIT_TIMEOUT: int = self._win32event.WAIT_TIMEOUT

        self.PIPE_ACCESS_DUPLEX: int = self._win32pipe.PIPE_ACCESS_DUPLEX
        self.PIPE_TYPE_MESSAGE: int = self._win32pipe.PIPE_TYPE_MESSAGE
        self.PIPE_READMODE_MESSAGE: int = self._win32pipe.PIPE_READMODE_MESSAGE
        self.PIPE_WAIT: int = self._win32pipe.PIPE_WAIT
        self.PIPE_UNLIMITED_INSTANCES: int = self._win32pipe.PIPE_UNLIMITED_INSTANCES

        self.FILE_FLAG_OVERLAPPED: int = self._win32file.FILE_FLAG_OVERLAPPED
        self.GENERIC_READ: int = self._win32file.GENERIC_READ
        self.GENERIC_WRITE: int = self._win32file.GENERIC_WRITE
        self.OPEN_EXISTING: int = self._win32file.OPEN_EXISTING

    @typ.override
    def create_event(self) -> object:
        """Create the unnamed, manual-reset event the transport waits on.

        Returns
        -------
        object
            The new event handle.
        """
        # CreateEvent(security, manual_reset, initial_state, name).
        return self._win32event.CreateEvent(
            None,
            True,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
            False,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
            None,
        )

    @typ.override
    def set_event(self, handle: object) -> None:
        """Signal *handle* so every waiter wakes."""
        self._win32event.SetEvent(handle)

    @typ.override
    def wait(self, handles: cabc.Sequence[object], timeout_ms: int) -> int:
        """Wait until any of *handles* is signalled, or *timeout_ms* expires.

        Returns
        -------
        int
            The ``WaitForMultipleObjects`` result code.
        """
        # WaitForMultipleObjects(handles, wait_all, timeout_ms).
        return self._win32event.WaitForMultipleObjects(
            list(handles),
            False,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
            timeout_ms,
        )

    @typ.override
    def create_overlapped(self) -> tuple[object, object]:
        """Build an ``OVERLAPPED`` with a dedicated manual-reset event.

        Returns
        -------
        tuple[object, object]
            The overlapped structure and its event handle.
        """
        overlapped = self._pywintypes.OVERLAPPED()
        # The overlapped event has the same manual-reset semantics the
        # transport wants for its shutdown event, so build it the same way.
        event = self.create_event()
        overlapped.hEvent = event
        return overlapped, event

    @typ.override
    def get_overlapped_result(self, handle: object, overlapped: object) -> int:
        """Wait for *overlapped* to complete and return the bytes transferred.

        Returns
        -------
        int
            The number of bytes the completed operation transferred.
        """
        # GetOverlappedResult(handle, overlapped, wait).
        return self._win32file.GetOverlappedResult(
            handle,
            overlapped,
            True,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes this flag positionally
        )

    @typ.override
    def cancel_io(self, handle: object, overlapped: object) -> None:
        """Request cancellation of the operation *overlapped* describes."""
        self._win32file.CancelIoEx(handle, overlapped)

    @typ.override
    def create_named_pipe(self, spec: NamedPipeCreateSpec) -> object:
        """Create one named-pipe instance described by *spec*.

        Returns
        -------
        object
            The new pipe instance handle.
        """
        return self._win32pipe.CreateNamedPipe(
            spec.name,
            spec.open_mode,
            spec.pipe_mode,
            spec.max_instances,
            spec.out_buffer_size,
            spec.in_buffer_size,
            spec.default_timeout,
            spec.security_attributes,
        )

    @typ.override
    def connect_named_pipe(self, handle: object, overlapped: object) -> int:
        """Start connecting *handle* to a client and report the initial status.

        Returns
        -------
        int
            ``ERROR_IO_PENDING`` when the connect is asynchronous, otherwise
            the status reported synchronously.
        """
        return self._win32pipe.ConnectNamedPipe(handle, overlapped)

    @typ.override
    def disconnect_named_pipe(self, handle: object) -> None:
        """Disconnect *handle* from its client."""
        self._win32pipe.DisconnectNamedPipe(handle)

    @typ.override
    def read_file(
        self, handle: object, buffer: cabc.Buffer, overlapped: object
    ) -> tuple[int, cabc.Buffer]:
        """Start an overlapped read and report the immediate status.

        Returns
        -------
        tuple[int, cabc.Buffer]
            The immediate status and the buffer filled by the read.
        """
        return self._win32file.ReadFile(handle, buffer, overlapped)

    @typ.override
    def allocate_read_buffer(self, size: int) -> cabc.Buffer:
        """Allocate a writable buffer of *size* bytes for an overlapped read.

        Returns
        -------
        collections.abc.Buffer
            A zeroed buffer the kernel can write into.
        """
        return self._win32file.AllocateReadBuffer(size)

    @typ.override
    def create_file(self, spec: PipeFileOpenSpec) -> object:
        """Open the pipe described by *spec*, used only to wake the accept loop.

        Returns
        -------
        object
            The opened handle.
        """
        return self._win32file.CreateFile(
            spec.file_name,
            spec.desired_access,
            spec.share_mode,
            spec.security_attributes,
            spec.creation_disposition,
            spec.flags_and_attributes,
            spec.template_file,
        )

    @typ.override
    def close_handle(self, handle: object) -> None:
        """Release *handle*."""
        self._win32file.CloseHandle(handle)


__all__ = ["Win32NamedPipeBackend"]
