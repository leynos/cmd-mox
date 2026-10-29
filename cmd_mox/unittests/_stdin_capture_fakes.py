"""Reusable stream and clock doubles for bounded stdin capture tests."""

from __future__ import annotations

import io
import os
import threading
import types
import typing as typ

from cmd_mox import _stdin_capture as capture

if typ.TYPE_CHECKING:
    import pytest


class _Clock:
    """Mutable monotonic clock advanced explicitly by each test."""

    def __init__(self, now: float = 0.0) -> None:
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


def _install_completion_clock(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, advance: float
) -> None:
    """Make the capture module build a clock-advancing completion event.

    Both the real :class:`threading.Thread` and the double are installed
    together so worker startup keeps its real scheduling behaviour.
    """
    event = _ClockAdvancingEvent(clock, advance)
    monkeypatch.setattr(
        capture,
        "threading",
        types.SimpleNamespace(Event=lambda: event, Thread=threading.Thread),
    )


class _StreamError(RuntimeError):
    """Failure raised by a test stream."""


def _read(stream: object, timeout: float, clock: _Clock) -> str:
    """Capture stdin for a test using an explicit clock.

    Returns
    -------
    str
        The captured text.
    """
    return capture.read_stdin(
        typ.cast("capture._StdinStream", stream), timeout, clock=clock
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
