"""Reusable stream and clock doubles for bounded stdin capture tests."""

from __future__ import annotations

import dataclasses
import io
import os
import threading
import typing as typ

from cmd_mox import _stdin_capture as capture
from cmd_mox._stdin_system import (
    SYSTEM_STDIN,
    CompletionEvent,
    StdinSystem,
    WindowsStdinSystem,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from cmd_mox._stdin_system import _Poller


class _Unset:
    """Sentinel marking a double operation that delegates to production."""


_UNSET = _Unset()


class _Clock:
    """Mutable monotonic clock advanced explicitly by each test."""

    def __init__(self, now: float = 0.0) -> None:
        """Start the fake clock at *now* seconds."""
        self.now = now

    def __call__(self) -> float:
        """Return the current fake time.

        Returns
        -------
        float
            The time last assigned to :attr:`now`.
        """
        return self.now


class _Stream:
    """Non-tty text stream with no descriptor and no buffered reader."""

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, data: str = "", *, is_tty: bool = False) -> None:
        """Store the text payload and tty flag, with the read counter at zero."""
        self.data = data
        self.read_calls = 0
        self._is_tty = is_tty

    def isatty(self) -> bool:
        """Report the terminal status configured for this stream.

        Returns
        -------
        bool
            ``True`` when the double simulates a terminal.
        """
        return self._is_tty

    def read(self) -> str:
        """Return the configured text and count the read.

        Returns
        -------
        str
            The text supplied when the double was created.
        """
        self.read_calls += 1
        return self.data


class _NoFileno(_Stream):
    """Stream whose ``fileno`` reports that no descriptor exists."""

    @staticmethod
    def fileno() -> int:
        """Reject descriptor access the way ``StringIO`` does.

        Raises
        ------
        io.UnsupportedOperation
            Always, so callers take the missing-descriptor path.
        """
        msg = "no descriptor"
        raise io.UnsupportedOperation(msg)


class _BlockingStream(_Stream):
    """Stream whose read blocks until *release* is set."""

    def __init__(self, data: str) -> None:
        """Create a stream whose read blocks until its release event is set."""
        super().__init__(data)
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def read(self) -> str:
        """Block until released, then return the configured text.

        Returns
        -------
        str
            The text supplied when the double was created.
        """
        self.started.set()
        try:
            self.release.wait(5.0)
            return self.data
        finally:
            self.finished.set()


class _DescriptorStream(_Stream):
    """Stream exposing an explicit descriptor and optional buffer."""

    def __init__(self, descriptor: int, *, data: str = "") -> None:
        """Store the descriptor reported by ``fileno`` and the buffered text."""
        super().__init__(data)
        self._descriptor = descriptor

    def fileno(self) -> int:
        """Return the descriptor supplied to this double.

        Returns
        -------
        int
            The descriptor configured for the double.
        """
        return self._descriptor


class _PollerOSError(OSError):
    """I/O failure raised by a scripted poller."""


class _ErrorPoller:
    """Poller whose registration succeeds but whose wait fails."""

    @staticmethod
    def poll(timeout: int, /) -> list[tuple[int, int]]:
        """Fail the readiness wait.

        Raises
        ------
        _PollerOSError
            Always, so callers can assert the failure propagates.
        """
        msg = "blocked poll failed"
        raise _PollerOSError(msg)


class _ClockAdvancingEvent:
    """Completion event that moves a fake clock when the waiter observes it.

    ``Event.wait`` reports only that the worker finished, not when. *advance*
    is how much fake time is charged to the waiting thread before it resumes
    and checks the deadline, which models a waiter that was descheduled while
    its worker completed.
    """

    def __init__(self, clock: _Clock, advance: float) -> None:
        """Store the fake clock and the time charged to each successful wait."""
        self._finished = threading.Event()
        self._clock = clock
        self._advance = advance

    def set(self) -> None:
        """Signal completion the way the real worker target does."""
        self._finished.set()

    def wait(self, timeout: float | None = None) -> bool:
        """Wait for the worker, charging *advance* fake seconds on success.

        Returns
        -------
        bool
            Whether the worker finished within *timeout*.
        """
        finished = self._finished.wait(timeout)
        if finished:
            self._clock.now += self._advance
        return finished


@dataclasses.dataclass(frozen=True, slots=True)
class _RecordingNativePoller:
    """Native ``select.poll`` double recording registration and probes."""

    events: list[tuple[int, int]] = dataclasses.field(default_factory=list)
    registered: list[tuple[int, int]] = dataclasses.field(default_factory=list)
    timeouts: list[int] = dataclasses.field(default_factory=list)

    def register(self, descriptor: int, mask: int) -> None:
        """Record the watched descriptor and its event mask."""
        self.registered.append((descriptor, mask))

    def poll(self, timeout: int, /) -> list[tuple[int, int]]:
        """Record the wait and return the scripted readiness events."""
        self.timeouts.append(timeout)
        return list(self.events)


@dataclasses.dataclass(frozen=True, slots=True)
class _WindowsSystemDouble:
    """Windows system double; unset members delegate to the production half."""

    windows_flag: bool | _Unset = _UNSET
    handle_override: object = _UNSET
    modules_override: tuple[object, object, object] | _Unset | None = _UNSET

    @property
    def is_windows(self) -> bool:
        """The injected platform flag, or the host's flag."""
        if isinstance(self.windows_flag, _Unset):
            return SYSTEM_STDIN.windows.is_windows
        return self.windows_flag

    def stdin_handle(self, stream: object) -> object | None:
        """Return the injected handle, or resolve it through the host."""
        if isinstance(self.handle_override, _Unset):
            return SYSTEM_STDIN.windows.stdin_handle(stream)
        return self.handle_override

    def modules(self) -> tuple[object, object, object] | None:
        """Return the injected pywin32 modules, or acquire them from the host."""
        if isinstance(self.modules_override, _Unset):
            return SYSTEM_STDIN.windows.modules()
        return self.modules_override


@dataclasses.dataclass(frozen=True, slots=True)
class _SystemDouble:
    """Stdin system double; unset operations delegate to the host system."""

    windows: WindowsStdinSystem = dataclasses.field(
        default_factory=_WindowsSystemDouble
    )
    create_poller_override: cabc.Callable[[int], _Poller | None] | _Unset = _UNSET
    read_override: cabc.Callable[[int, int], bytes] | _Unset = _UNSET
    regular_file_override: bool | _Unset = _UNSET
    event_factory: cabc.Callable[[], CompletionEvent] | _Unset = _UNSET
    worker_factory: cabc.Callable[..., threading.Thread] | _Unset = _UNSET

    def create_poller(self, descriptor: int) -> _Poller | None:
        """Return the injected poller, or build the production one."""
        if isinstance(self.create_poller_override, _Unset):
            return SYSTEM_STDIN.create_poller(descriptor)
        return self.create_poller_override(descriptor)

    def read(self, descriptor: int, size: int) -> bytes:
        """Return the injected read result, or read from the host."""
        if isinstance(self.read_override, _Unset):
            return SYSTEM_STDIN.read(descriptor, size)
        return self.read_override(descriptor, size)

    def is_regular_file(self, descriptor: int) -> bool:
        """Return the injected descriptor kind, or query the host."""
        if isinstance(self.regular_file_override, _Unset):
            return SYSTEM_STDIN.is_regular_file(descriptor)
        return self.regular_file_override

    def create_event(self) -> CompletionEvent:
        """Return the injected event, or create a real one."""
        if isinstance(self.event_factory, _Unset):
            return SYSTEM_STDIN.create_event()
        return self.event_factory()

    def create_worker(
        self, target: cabc.Callable[[], object], *, name: str
    ) -> threading.Thread:
        """Return the injected worker, or create a real daemon worker.

        Returns
        -------
        threading.Thread
            An unstarted worker for the capture to start and track.
        """
        if isinstance(self.worker_factory, _Unset):
            return SYSTEM_STDIN.create_worker(target, name=name)
        return self.worker_factory(target, name=name)


def _system_with_completion_clock(
    clock: _Clock,
    advance: float,
    *,
    windows: WindowsStdinSystem | None = None,
) -> _SystemDouble:
    """Build a system whose completion events advance *clock* on success.

    Real worker creation is kept, so the fake clock only models a waiter that
    resumed after its worker finished.

    Returns
    -------
    _SystemDouble
        Double backed by the real worker factory and the advancing events.
    """
    return _SystemDouble(
        windows=windows if windows is not None else _WindowsSystemDouble(),
        event_factory=lambda: _ClockAdvancingEvent(clock, advance),
    )


class _StreamError(RuntimeError):
    """Failure raised by a test stream."""


def _read(
    stream: object,
    timeout: float,
    clock: _Clock,
    *,
    system: StdinSystem = SYSTEM_STDIN,
) -> str:
    """Capture stdin for a test using an explicit clock and system.

    Returns
    -------
    str
        The captured text.
    """
    return capture.read_stdin(
        typ.cast("capture._StdinStream", stream),
        timeout,
        clock=clock,
        system=system,
    )


def _open_pipe_stream(payload: bytes) -> typ.IO[str]:
    """Return a text stream over a pipe already primed with *payload*.

    Returns
    -------
    typ.IO[str]
        A text stream whose writer has been closed, so it reads through EOF.
    """
    read_fd, write_fd = os.pipe()
    os.write(write_fd, payload)
    os.close(write_fd)
    return os.fdopen(read_fd, encoding="utf-8", errors="strict")
