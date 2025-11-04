"""Shared helpers for Windows named pipe IPC."""

from __future__ import annotations

import dataclasses as dc
import importlib
import logging
import threading
import typing as t

from cmd_mox.environment import IS_WINDOWS

logger = logging.getLogger(__name__)


@dc.dataclass(slots=True)
class Win32Modules:
    """Container for lazily imported pywin32 modules."""

    pywintypes: t.Any
    win32file: t.Any
    win32pipe: t.Any
    winerror: t.Any


@dc.dataclass(slots=True)
class NamedPipeAPI:
    """High-level operations for Windows named pipes."""

    pywintypes: t.Any
    win32file: t.Any
    win32pipe: t.Any
    winerror: t.Any

    def open_client_handle(self, pipe_name: str, timeout: float) -> t.Any:  # noqa: ANN401
        """Return a handle to *pipe_name* honouring *timeout* in seconds."""
        try:
            return self.win32file.CreateFile(
                pipe_name,
                self.win32file.GENERIC_READ | self.win32file.GENERIC_WRITE,
                0,
                None,
                self.win32file.OPEN_EXISTING,
                0,
                None,
            )
        except self.pywintypes.error as exc:  # type: ignore[union-attr]
            if exc.winerror in {
                self.winerror.ERROR_PIPE_BUSY,
                self.winerror.ERROR_FILE_NOT_FOUND,
            }:
                wait_ms = max(1, int(timeout * 1000))
                try:
                    self.win32pipe.WaitNamedPipe(pipe_name, wait_ms)
                except self.pywintypes.error:
                    logger.debug("WaitNamedPipe(%s) failed", pipe_name, exc_info=True)
            raise

    def read_from_pipe(self, handle: t.Any) -> bytes:  # noqa: ANN401
        """Read all bytes available from *handle*."""
        chunks = bytearray()
        while True:
            try:
                status, chunk = self.win32file.ReadFile(handle, 65536)
            except self.pywintypes.error as exc:
                if exc.winerror == self.winerror.ERROR_BROKEN_PIPE:
                    break
                raise
            chunks.extend(chunk)
            if status == 0:
                break
            if status != self.win32pipe.ERROR_MORE_DATA:
                break
        return bytes(chunks)

    def write_to_pipe(self, handle: t.Any, payload: bytes) -> None:  # noqa: ANN401
        """Write *payload* to *handle* and flush buffers."""
        self.win32file.WriteFile(handle, payload)
        self.win32file.FlushFileBuffers(handle)

    def close_handle(self, handle: t.Any) -> None:  # noqa: ANN401
        """Close *handle* suppressing errors."""
        try:
            self.win32file.CloseHandle(handle)
        except OSError as exc:  # pragma: no cover - defensive cleanup
            logger.debug("CloseHandle failed with OSError: %r", exc, exc_info=True)

    def disconnect_named_pipe(self, handle: t.Any) -> None:  # noqa: ANN401
        """Disconnect *handle* if possible."""
        try:
            self.win32pipe.DisconnectNamedPipe(handle)
        except OSError:  # pragma: no cover - defensive cleanup
            logger.debug("DisconnectNamedPipe failed", exc_info=True)

    def create_named_pipe(self, socket_name: str, timeout: float) -> t.Any | None:  # noqa: ANN401
        """Create a message-mode named pipe for *socket_name*."""
        try:
            return self.win32pipe.CreateNamedPipe(
                socket_name,
                self.win32pipe.PIPE_ACCESS_DUPLEX,
                self.win32pipe.PIPE_TYPE_MESSAGE
                | self.win32pipe.PIPE_READMODE_MESSAGE
                | self.win32pipe.PIPE_WAIT,
                self.win32pipe.PIPE_UNLIMITED_INSTANCES,
                65536,
                65536,
                int(timeout * 1000),
                None,
            )
        except self.pywintypes.error:  # pragma: no cover - defensive logging
            logger.exception("Failed to create named pipe %s", socket_name)
            return None

    def connect_named_pipe(self, handle: t.Any) -> None:  # noqa: ANN401
        """Block until a client connects to *handle*."""
        self.win32pipe.ConnectNamedPipe(handle, None)

    def set_message_mode(self, handle: t.Any) -> None:  # noqa: ANN401
        """Configure *handle* to operate in message read mode."""
        self.win32pipe.SetNamedPipeHandleState(
            handle,
            self.win32pipe.PIPE_READMODE_MESSAGE,
            None,
            None,
        )

    def poke_server(self, socket_name: str) -> None:
        """Wake the server by connecting a new client handle."""
        try:
            handle = self.win32file.CreateFile(
                socket_name,
                self.win32file.GENERIC_READ | self.win32file.GENERIC_WRITE,
                0,
                None,
                self.win32file.OPEN_EXISTING,
                0,
                None,
            )
        except OSError as exc:  # pragma: no cover - best-effort wake up
            logger.debug(
                "Failed to wake named pipe %s: %r", socket_name, exc, exc_info=True
            )
            return
        self.close_handle(handle)

    def is_retryable_client_error(self, exc: Exception) -> bool:
        """Return ``True`` when *exc* represents a retryable client failure."""
        if not isinstance(exc, self.pywintypes.error):
            return False
        retryable = {
            self.winerror.ERROR_PIPE_BUSY,
            self.winerror.ERROR_FILE_NOT_FOUND,
        }
        return getattr(exc, "winerror", None) in retryable


_MODULES_LOCK = threading.Lock()
_CACHED_MODULES: Win32Modules | None = None
_MODULES_INITIALISED = False


def _load_modules() -> Win32Modules | None:
    if not IS_WINDOWS:
        return None
    try:
        return Win32Modules(
            pywintypes=importlib.import_module("pywintypes"),
            win32file=importlib.import_module("win32file"),
            win32pipe=importlib.import_module("win32pipe"),
            winerror=importlib.import_module("winerror"),
        )
    except ModuleNotFoundError:  # pragma: no cover - best effort import
        return None


def _get_modules() -> Win32Modules | None:
    global _MODULES_INITIALISED, _CACHED_MODULES
    with _MODULES_LOCK:
        if not _MODULES_INITIALISED:
            _CACHED_MODULES = _load_modules()
            _MODULES_INITIALISED = True
        return _CACHED_MODULES


def optional_named_pipe_api() -> NamedPipeAPI | None:
    """Return a cached :class:`NamedPipeAPI` when available."""
    modules = _get_modules()
    if modules is None:
        return None
    return NamedPipeAPI(
        pywintypes=modules.pywintypes,
        win32file=modules.win32file,
        win32pipe=modules.win32pipe,
        winerror=modules.winerror,
    )


def require_named_pipe_api(context: str) -> NamedPipeAPI:
    """Return a :class:`NamedPipeAPI` or raise when pywin32 is missing."""
    api = optional_named_pipe_api()
    if api is None:
        msg = (
            f"pywin32 is required for {context}; install 'pywin32' on Windows to "
            "enable IPC."
        )
        raise RuntimeError(msg)
    return api


__all__ = [
    "NamedPipeAPI",
    "optional_named_pipe_api",
    "require_named_pipe_api",
]
