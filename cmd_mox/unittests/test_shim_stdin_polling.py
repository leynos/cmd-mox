"""Tests for poll/select-based stdin readiness and timeouts."""

from __future__ import annotations

import os
import stat
import sys
import typing as typ

import pytest

from cmd_mox import shim
from cmd_mox.ipc import _observability
from cmd_mox.shim import _create_invocation
from cmd_mox.unittests._shim_test_support import (
    _assert_exit_code,
    _BufferedInput,
    _DescriptorStdin,
    _InjectedOSError,
)


class _InjectedBufferedReader:
    """Return scripted bytes through the injected buffered-read path."""

    def __init__(self) -> None:
        """Prepare UTF-8 input followed by EOF."""
        self._chunks = iter([b"payload\r\n", b""])

    def read1(self, _size: int) -> bytes:
        """Return the next scripted chunk.

        Returns
        -------
        bytes
            One chunk of test input, or the empty EOF marker.
        """
        return next(self._chunks)


class _InjectedStdin:
    """Expose the minimal text-stream protocol for synthetic stdin."""

    def __init__(self, descriptor: int) -> None:
        """Bind the synthetic descriptor and buffered input."""
        self._descriptor = descriptor
        self.buffer = _InjectedBufferedReader()

    @property
    def encoding(self) -> str:
        """Decoder encoding for the scripted input bytes."""
        return "utf-8"

    @property
    def errors(self) -> str:
        """Decoder error policy for the scripted input bytes."""
        return "strict"

    def fileno(self) -> int:
        """Return the synthetic descriptor.

        Returns
        -------
        int
            The descriptor supplied at construction.
        """
        return self._descriptor

    def read(self) -> str:
        """Return a sentinel if the direct-read fallback is selected.

        Returns
        -------
        str
            A value that cannot match the expected buffered payload.
        """
        return "unexpected direct read"


class _InjectedReadyPoller:
    """Report readiness for the descriptor supplied at construction."""

    def __init__(self, descriptor: int) -> None:
        """Bind the descriptor to report as readable."""
        self._descriptor = descriptor

    def poll(self, _timeout: int) -> list[tuple[int, int]]:
        """Report the bound descriptor as ready.

        Returns
        -------
        list[tuple[int, int]]
            A single readiness event for the synthetic descriptor.
        """
        return [(self._descriptor, 1)]


class _CountingClock:
    """Return a stable deadline clock and record each observation."""

    def __init__(self) -> None:
        """Start the clock-read counter at zero."""
        self.reads = 0

    def __call__(self) -> float:
        """Return a time before the deadline.

        Returns
        -------
        float
            Zero, leaving the synthetic deadline in the future.
        """
        self.reads += 1
        return 0.0


def _create_ready_poller(descriptor: int) -> _InjectedReadyPoller:
    """Create a poller for the descriptor returned by injected stdin.

    Returns
    -------
    _InjectedReadyPoller
        Poller that reports the supplied descriptor as ready.
    """
    return _InjectedReadyPoller(descriptor)


def _fifo_stat(_descriptor: int) -> os.stat_result:
    """Classify injected stdin as a pipe rather than a regular file.

    Returns
    -------
    os.stat_result
        A synthetic stat result whose mode denotes a FIFO.
    """
    return os.stat_result((stat.S_IFIFO, 0, 0, 0, 0, 0, 0, 0, 0, 0))


def _unexpected_descriptor_read(_descriptor: int, _size: int) -> typ.NoReturn:
    """Fail if buffered stdin unexpectedly reaches raw descriptor reads."""
    pytest.fail("buffered stdin unexpectedly used the descriptor reader")


class _FailingPoll:
    """Poller that accepts setup but fails on an active wait."""

    def register(self, _fd: int, _events: int) -> None:
        """Accept the descriptor so failure occurs during the real wait."""

    def poll(self, _timeout: int) -> list[tuple[int, int]]:
        """Raise the injected I/O error after successful setup.

        Raises
        ------
        _InjectedOSError
            Simulated poll failure.
        """
        raise _InjectedOSError("hidden")


class _FailingSelect:
    """Select adapter that passes its setup probe and then fails."""

    def __init__(self) -> None:
        """Track the setup probe separately from the active wait."""
        self.calls = 0

    def __call__(
        self,
        _read_fds: list[int],
        _write_fds: list[int],
        _exception_fds: list[int],
        _timeout: float,
    ) -> tuple[list[int], list[int], list[int]]:
        """Return once for setup, then raise during the bounded wait.

        Returns
        -------
        tuple[list[int], list[int], list[int]]
            Empty readiness lists during the setup probe.

        Raises
        ------
        _InjectedOSError
            Simulated select failure.
        """
        self.calls += 1
        if self.calls == 1:
            return [], [], []
        raise _InjectedOSError("hidden")


@pytest.fixture(params=("poll", "select"), ids=("poll", "select"))
def stdin_poll_error_buffer(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    stdin_pipe_descriptor: int,
) -> _BufferedInput:
    """Prepare stdin and an adapter that fails only during active polling.

    Returns
    -------
    _BufferedInput
        The input buffer, for verifying that no bytes were consumed.
    """
    buffer = _BufferedInput([b"must not be read"])
    monkeypatch.setattr(
        sys,
        "stdin",
        _DescriptorStdin(stdin_pipe_descriptor, buffer=buffer),
    )
    monkeypatch.setattr(sys, "argv", ["shim"])
    if request.param == "poll":
        monkeypatch.setattr(shim._shim_stdin.select, "poll", _FailingPoll)
    else:
        monkeypatch.setattr(shim._shim_stdin.select, "poll", None)
        monkeypatch.setattr(
            shim._shim_stdin.select,
            "select",
            _FailingSelect(),
        )
    return buffer


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_reports_stdin_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stdin_pipe_descriptor: int,
) -> None:
    """A non-tty stream that never becomes readable exits with an IPC error."""

    class _NeverReadablePoll:
        def register(self, _fd: int, _events: int) -> None:
            """Accept the descriptor without scheduling a readiness event."""
            pass

        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            """Keep the descriptor unreadable until the deadline expires.

            Returns
            -------
            list[tuple[int, int]]
                No readiness events.
            """
            return []

    buffer = _BufferedInput([b"unexpected payload"])
    monkeypatch.setattr(
        sys,
        "stdin",
        _DescriptorStdin(stdin_pipe_descriptor, buffer=buffer),
    )
    monkeypatch.setattr(shim._shim_stdin.select, "poll", _NeverReadablePoll)
    monkeypatch.setattr(sys, "argv", ["shim"])

    with pytest.raises(SystemExit) as exc:
        _create_invocation("shim", timeout=0.01)

    _assert_exit_code(exc, 1)
    assert buffer.read_calls == 0, "Buffered input was read before readiness"
    assert "IPC error: timed out reading stdin" in capsys.readouterr().err, (
        "stdin timeout must produce an IPC diagnostic"
    )


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_reports_stdin_poll_error(
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    stdin_poll_error_buffer: _BufferedInput,
) -> None:
    """Active poller failures use the bounded stdin diagnostic boundary."""
    counters_before = _observability.counter_snapshot()

    with (
        caplog.at_level("INFO", logger="cmd_mox.shim"),
        _observability.capture_events() as events,
        pytest.raises(SystemExit) as exc,
    ):
        _create_invocation("shim", timeout=1.0)

    _assert_exit_code(exc, 1)
    diagnostic = capsys.readouterr().err
    assert "IPC error: stdin read failed" in diagnostic, (
        "polling failure did not produce a controlled stdin diagnostic"
    )
    assert "hidden" not in diagnostic, "polling exception details must not reach stderr"
    assert "hidden" not in caplog.text, "polling exception details must not reach logs"
    assert stdin_poll_error_buffer.read_calls == 0, (
        "stdin bytes were read after poller failure"
    )
    [event] = events
    assert event.operation == "ipc.shim.stdin_read", "Wrong stdin event operation"
    assert event.outcome == "read_failed", "Polling failure was not reported once"
    assert event.error_category == "OSError", "Wrong polling error category"
    counts_after = _observability.counter_snapshot()
    assert (
        counts_after.get(event.key(), 0) == counters_before.get(event.key(), 0) + 1
    ), "polling failure did not increment its bounded event counter once"


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_caps_large_poll_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stdin_pipe_descriptor: int,
) -> None:
    """A large valid timeout stays within the poll API's millisecond range."""

    class _NeverReadablePoll:
        def __init__(self) -> None:
            """Prepare to record the millisecond waits requested by the shim."""
            self.timeouts: list[int] = []

        def register(self, _fd: int, _events: int) -> None:
            """Accept the descriptor without producing readiness."""
            pass

        def poll(self, timeout: int) -> list[tuple[int, int]]:
            """Record the capped wait while keeping stdin unreadable.

            Returns
            -------
            list[tuple[int, int]]
                No readiness events.
            """
            self.timeouts.append(timeout)
            return []

    poller = _NeverReadablePoll()
    monkeypatch.setattr(sys, "stdin", _DescriptorStdin(stdin_pipe_descriptor))
    monkeypatch.setattr(shim._shim_stdin.select, "poll", lambda: poller)
    monotonic_values = iter([0.0, 0.0, 1e308])
    monkeypatch.setattr(shim.time, "monotonic", lambda: next(monotonic_values))

    with pytest.raises(SystemExit):
        _create_invocation("shim", timeout=1e308)

    assert poller.timeouts == [shim._shim_stdin.MAX_POLL_TIMEOUT_MS], (
        "large timeouts must be capped before conversion to milliseconds"
    )
    assert "IPC error: timed out reading stdin" in capsys.readouterr().err, (
        "the capped poll path must retain the controlled timeout diagnostic"
    )


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_uses_select_when_poll_is_unavailable(
    monkeypatch: pytest.MonkeyPatch, stdin_pipe_descriptor: int
) -> None:
    """A waitable POSIX descriptor remains bounded without ``select.poll``."""
    select_timeouts: list[float] = []

    def fake_select(
        readers: list[int],
        _writers: list[int],
        _errors: list[int],
        timeout: float,
    ) -> tuple[list[int], list[int], list[int]]:
        """Capture the wait and report the supplied readable descriptors.

        Returns
        -------
        tuple[list[int], list[int], list[int]]
            Readable descriptors and empty writable/error descriptor lists.
        """
        select_timeouts.append(timeout)
        return readers, [], []

    chunks = iter([b"payload", b""])
    monkeypatch.setattr(sys, "stdin", _DescriptorStdin(stdin_pipe_descriptor))
    monkeypatch.delattr(shim._shim_stdin.select, "poll")
    monkeypatch.setattr(shim._shim_stdin.select, "select", fake_select)
    monkeypatch.setattr(shim._shim_stdin.os, "read", lambda _fd, _size: next(chunks))
    monkeypatch.setattr(sys, "argv", ["shim"])

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "payload", "select fallback should capture stdin"
    assert select_timeouts[0] == 0, "select fallback should probe readiness immediately"
    assert all(0 < timeout <= 1.0 for timeout in select_timeouts[1:]), (
        "select fallback waits should remain within the configured deadline"
    )


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_preserves_piped_newlines(
    monkeypatch: pytest.MonkeyPatch, stdin_pipe_descriptor: int
) -> None:
    """Piped text preserves POSIX TextIO newline sequences across byte chunks."""

    class _ReadablePoll:
        def register(self, _fd: int, _events: int) -> None:
            """Accept registration for the synthetic readable descriptor."""
            pass

        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            """Report stdin readable so the test can deliver newline fragments.

            Returns
            -------
            list[tuple[int, int]]
                One readiness event for stdin.
            """
            return [(stdin_pipe_descriptor, shim._shim_stdin.select.POLLIN)]

    chunks = iter([b"first\r", b"\nsecond\rthird", b""])
    monkeypatch.setattr(sys, "stdin", _DescriptorStdin(stdin_pipe_descriptor))
    monkeypatch.setattr(shim._shim_stdin.select, "poll", _ReadablePoll)
    monkeypatch.setattr(shim._shim_stdin.os, "read", lambda _fd, _size: next(chunks))
    monkeypatch.setattr(sys, "argv", ["shim"])

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "first\r\nsecond\rthird", (
        "bounded stdin reads must preserve POSIX newline sequences"
    )


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_stdin_reader_uses_injected_source_and_clock() -> None:
    """The reader uses the supplied stream, polling, descriptor, and clock seam."""
    descriptor = 123
    clock = _CountingClock()
    source = shim._shim_stdin._StdinSource(
        stream=_InjectedStdin(descriptor),
        create_poller=_create_ready_poller,
        read_descriptor=_unexpected_descriptor_read,
        stat_descriptor=_fifo_stat,
    )
    result = shim._shim_stdin._read_stdin_until_eof(
        1.0,
        deadline=1.0,
        on_error=lambda exc: pytest.fail(f"injected stdin source failed: {exc}"),
        dependencies=shim._shim_stdin._StdinReadDependencies(
            source=source,
            clock=clock,
        ),
    )

    assert result == "payload\r\n", "Injected stdin source changed input text"
    assert clock.reads > 0, "Reader ignored the injected monotonic clock"
