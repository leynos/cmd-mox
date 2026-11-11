"""Narrow system boundary for bounded stdin capture.

Bounded stdin capture reaches the operating system for four things: creating a
poller, reading a descriptor, querying whether a descriptor is a regular file,
and, on Windows, resolving a handle and importing pywin32. It also reaches the
process for the daemon workers and completion events that bound streams which
cannot be polled.

Keeping those operations behind one injected :class:`StdinSystem` gives tests a
narrow seam: a strategy test can supply a poller, a read script or a worker
double without replacing ``os``, ``select`` or ``threading`` for the whole
process. The production implementation, :data:`SYSTEM_STDIN`, delegates to the
real modules and is the default everywhere the capture module is called.

Examples
--------
``system = system_stdin()``
``poller = system.create_poller(descriptor)``
"""

from __future__ import annotations

import importlib
import os
import select
import stat
import threading
import typing as typ

from cmd_mox import _path_utils as path_utils

if typ.TYPE_CHECKING:
    import collections.abc as cabc

type _SelectCall = cabc.Callable[
    [cabc.Sequence[int], cabc.Sequence[int], cabc.Sequence[int], float],
    tuple[list[int], list[int], list[int]],
]


class _Poller(typ.Protocol):
    """Minimal polling interface required by the bounded stdin reader."""

    def poll(self, timeout: int, /) -> list[tuple[int, int]]:
        """Wait up to *timeout* milliseconds for descriptor events."""


class _NativePoller(typ.Protocol):
    """Event-mask interface exposed by ``select.poll``."""

    def register(self, descriptor: int, mask: int, /) -> None:
        """Watch *descriptor* for the event bits in *mask*."""

    def poll(self, timeout: int, /) -> list[tuple[int, int]]:
        """Wait up to *timeout* milliseconds and return readiness events."""


class CompletionEvent(typ.Protocol):
    """Signalling primitive a worker sets when its read finishes."""

    def set(self) -> None:
        """Report that the worker finished."""

    def wait(self, timeout: float | None = None) -> bool:
        """Wait up to *timeout* seconds for completion."""


class _DescribedStream(typ.Protocol):
    """Stream that can expose the descriptor behind its text interface."""

    def fileno(self) -> int:
        """Return the stream's file descriptor."""


class _Msvcrt(typ.Protocol):
    """Subset of ``msvcrt`` needed to resolve a CRT descriptor to a handle."""

    def get_osfhandle(self, descriptor: int) -> int:
        """Return the Windows handle behind *descriptor*."""


class WindowsStdinSystem(typ.Protocol):
    """Windows operations needed by the Windows capture strategy."""

    @property
    def is_windows(self) -> bool:
        """Whether the host runs Windows."""

    def stdin_handle(self, stream: object) -> object | None:
        """Return the Windows handle behind *stream*, or ``None``."""

    def modules(self) -> tuple[object, object, object] | None:
        """Return ``(win32file, win32api, pywintypes)`` when available."""


class StdinSystem(typ.Protocol):
    """Operating-system and process operations used by stdin capture."""

    @property
    def windows(self) -> WindowsStdinSystem:
        """Windows-specific operations behind their own narrow half."""

    def create_poller(self, descriptor: int) -> _Poller | None:
        """Return a poller for *descriptor*, or ``None`` when it is unpollable."""

    def read(self, descriptor: int, size: int) -> bytes:
        """Read up to *size* bytes from *descriptor*."""

    def is_regular_file(self, descriptor: int) -> bool:
        """Report whether *descriptor* is backed by a regular file.

        Implementations must not raise when the descriptor's status cannot be
        queried. They report ``False`` so the capture keeps the polled read
        that already accepted the descriptor instead of moving it onto a
        worker for no reason.
        """

    def create_worker(
        self, target: cabc.Callable[[], object], *, name: str
    ) -> threading.Thread:
        """Create an unstarted daemon worker running *target*."""

    def create_event(self) -> CompletionEvent:
        """Create a fresh completion event."""


class _SelectPoller:
    """Adapt ``select.select`` to the polling interface used by the shim."""

    def __init__(self, descriptor: int, select_call: _SelectCall | None = None) -> None:
        """Bind the descriptor watched by this adapter and its select call."""
        self.descriptor = descriptor
        self._select_call = select_call

    def poll(self, timeout: int, /) -> list[tuple[int, int]]:
        """Wait up to *timeout* milliseconds for stdin to become readable.

        Returns
        -------
        list[tuple[int, int]]
            A single readiness event when stdin is ready, or an empty list on
            timeout.

        Raises
        ------
        ValueError
            If *descriptor* cannot be watched by ``select``: it is negative, or
            at or above ``FD_SETSIZE``. Propagating it is what lets
            :func:`_create_stdin_poller` reject the adapter and fall back to a
            direct read; swallowing it would hand back a poller that can never
            report readiness, so readable input would end in a spurious
            timeout.
        """  # ruff: ignore[docstring-extraneous-exception] - select() raises it, so no in-body raise is visible
        # select.select raises ValueError for a descriptor it cannot watch. It
        # must reach _create_stdin_poller so that probe rejects this adapter.
        select_call = select.select if self._select_call is None else self._select_call
        readable, _, _ = select_call([self.descriptor], [], [], timeout / 1_000)
        return [(self.descriptor, 1)] if readable else []


def _create_stdin_poller(
    descriptor: int,
    *,
    native_poll: cabc.Callable[[], _NativePoller] | None = None,
    select_call: _SelectCall | None = None,
) -> _Poller | None:
    """Choose a waitable interface, or signal the direct-read fallback.

    Parameters
    ----------
    descriptor:
        Descriptor whose readability must be waitable.
    native_poll:
        Factory for the native ``select.poll``; resolved from ``select`` at
        call time when omitted. Tests inject a double through this parameter
        instead of replacing the shared ``select`` module.
    select_call:
        ``select.select`` replacement passed to the fallback adapter.

    Returns
    -------
    _Poller or None
        A polling adapter that accepted a zero-timeout probe, or ``None`` when
        neither mechanism can wait on *descriptor*.
    """
    try:
        poll_factory = select.poll if native_poll is None else native_poll
        poller = poll_factory()
        poller.register(descriptor, select.POLLIN | select.POLLHUP)
        probe = poller.poll(0)
    except (AttributeError, OSError, TypeError, ValueError):
        pass
    else:
        if not any(mask & select.POLLNVAL for _descriptor, mask in probe):
            return poller

    try:
        select_poller = _SelectPoller(descriptor, select_call)
        select_poller.poll(0)
    except (AttributeError, OSError, TypeError, ValueError):
        return None
    return select_poller


def _is_windows() -> bool:
    """Return the live platform flag without caching it at import time."""
    return path_utils.IS_WINDOWS


def _stdin_handle(
    stream: object,
    *,
    import_module: cabc.Callable[[str], object] = importlib.import_module,
) -> object | None:
    """Resolve the Windows handle behind *stream*.

    Parameters
    ----------
    stream:
        Stream whose descriptor is resolved through ``msvcrt``.
    import_module:
        Module importer, injectable so tests can supply a failing or recording
        ``msvcrt`` without touching ``sys.modules``.

    Returns
    -------
    object or None
        The ``HANDLE`` for *stream*, or ``None`` when Windows cannot provide
        one, such as for an in-memory test double.
    """
    try:
        msvcrt = typ.cast("_Msvcrt", import_module("msvcrt"))
        return msvcrt.get_osfhandle(typ.cast("_DescribedStream", stream).fileno())
    except (AttributeError, ImportError, OSError, ValueError):
        return None


def _windows_modules(
    *,
    is_windows: cabc.Callable[[], bool] = _is_windows,
    import_module: cabc.Callable[[str], object] = importlib.import_module,
) -> tuple[object, object, object] | None:
    """Return ``(win32file, win32api, pywintypes)`` when pywin32 is present.

    Parameters
    ----------
    is_windows:
        Platform predicate read at call time, so a test can choose the host
        branch without replacing the shared ``path_utils`` flag.
    import_module:
        Module importer, injectable for contract tests that assert the import
        order or simulate missing pywin32.

    Returns
    -------
    tuple[object, object, object] or None
        The modules used to read and cancel a handle read, or ``None`` when
        they are unavailable.
    """
    if not is_windows():
        return None
    try:
        return (
            import_module("win32file"),
            import_module("win32api"),
            import_module("pywintypes"),
        )
    except ImportError:
        return None


class _OsWindowsStdinSystem:
    """Windows operations backed by the host platform and pywin32."""

    # Binding the factories directly keeps one implementation and still lets a
    # caller (or a test) pass their injectable keyword arguments.
    stdin_handle = staticmethod(_stdin_handle)
    modules = staticmethod(_windows_modules)

    @property
    def is_windows(self) -> bool:
        """The live platform flag, read on every access."""
        return path_utils.IS_WINDOWS


class _OsStdinSystem:
    """Operating-system and process operations backed by the running host."""

    def __init__(self) -> None:
        """Bind the Windows half that this system exposes."""
        self._windows: WindowsStdinSystem = _OsWindowsStdinSystem()

    @property
    def windows(self) -> WindowsStdinSystem:
        """The Windows-specific half of this system."""
        return self._windows

    # The production poller selection stays one module-level factory so the
    # injectable keyword arguments remain available to its direct callers.
    create_poller = staticmethod(_create_stdin_poller)

    @staticmethod
    def read(descriptor: int, size: int) -> bytes:
        """Read up to *size* bytes from *descriptor*."""
        return os.read(descriptor, size)

    @staticmethod
    def is_regular_file(descriptor: int) -> bool:
        """Report whether *descriptor* is backed by a regular file.

        Returns
        -------
        bool
            ``True`` only for a regular file. A descriptor whose status cannot
            be queried reports ``False``, so it keeps the polled read that
            already accepted it rather than being moved onto a worker for no
            reason.
        """
        try:
            return stat.S_ISREG(os.fstat(descriptor).st_mode)
        except OSError:
            return False

    @staticmethod
    def create_worker(
        target: cabc.Callable[[], object], *, name: str
    ) -> threading.Thread:
        """Create an unstarted daemon worker running *target*."""
        return threading.Thread(target=target, name=name, daemon=True)

    @staticmethod
    def create_event() -> CompletionEvent:
        """Create a fresh threading event for worker completion."""
        return threading.Event()


#: Stateless production system shared by every capture that does not inject one.
SYSTEM_STDIN: typ.Final[StdinSystem] = _OsStdinSystem()


def system_stdin() -> StdinSystem:
    """Return the stateless host-backed stdin system.

    Returns
    -------
    StdinSystem
        The process-wide :data:`SYSTEM_STDIN` implementation. It holds no
        per-capture state, so sharing one instance is safe.
    """
    return SYSTEM_STDIN
