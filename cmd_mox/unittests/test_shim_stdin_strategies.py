"""Tests for the shim's bounded fallback and POSIX polled stdin strategies."""

from __future__ import annotations

import os
import sys
import threading
import typing as typ

import pytest

from cmd_mox import _stdin_capture as capture
from cmd_mox import shim
from cmd_mox.ipc import _observability
from cmd_mox.shim import _create_invocation
from cmd_mox.unittests._shim_test_support import (
    _assert_exit_code,
    _BufferedInput,
    _DescriptorStdin,
    _DummyStdin,
    _InjectedOSError,
)

# --- Fallback strategy ------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_falls_back_to_direct_read_when_unpollable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported descriptors retain a direct read instead of failing."""

    class _UnpollableStdin(_DummyStdin):
        encoding = "utf-8"
        errors = "strict"

        def fileno(self) -> int:
            """Provide the descriptor passed to the poller probes.

            Returns
            -------
            int
                A synthetic descriptor for the poller setup.
            """
            return 123

    def fail_poll() -> typ.NoReturn:
        """Simulate an unavailable poll implementation.

        Raises
        ------
        _InjectedOSError
            Poll setup failure used to select the direct-read fallback.
        """
        raise _InjectedOSError

    def fail_select(*_args: object) -> typ.NoReturn:
        """Simulate an unavailable select implementation.

        Raises
        ------
        _InjectedOSError
            Select setup failure used to select the direct-read fallback.
        """
        raise _InjectedOSError

    stdin = _UnpollableStdin("payload", is_tty=False)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(capture.select, "poll", fail_poll)
    monkeypatch.setattr(capture.select, "select", fail_select)

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "payload", "direct-read fallback lost stdin payload"
    assert stdin.read_calls == 1, "direct-read fallback must read stdin exactly once"


def test_create_invocation_bounds_a_windows_direct_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A Windows stream without a handle expires instead of blocking forever."""
    release = threading.Event()
    started = threading.Event()

    class _StalledWindowsStdin:
        encoding = "utf-8"
        errors = "strict"

        def isatty(self) -> bool:
            """Report the non-interactive status of piped Windows stdin.

            Returns
            -------
            bool
                ``False``, so stdin is captured on a worker.
            """
            return False

        def read(self) -> str:
            """Block until the test releases the abandoned worker.

            Returns
            -------
            str
                The text supplied once the stream is released.
            """
            started.set()
            release.wait(5.0)
            return "late"

    monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(capture, "_stdin_handle", lambda _stream: None)
    monkeypatch.setattr(sys, "stdin", _StalledWindowsStdin())
    monkeypatch.setattr(sys, "argv", ["shim"])

    try:
        with pytest.raises(SystemExit) as exc:
            _create_invocation("shim", timeout=0.05)
    finally:
        release.set()

    _assert_exit_code(exc, 1)
    assert started.is_set(), "the Windows stdin read never started"
    assert "IPC error: stdin read timed out after 0.05 seconds" in (
        capsys.readouterr().err
    ), "Windows stdin expiry did not produce the bounded diagnostic"


# --- Polled strategy --------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_reports_stdin_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stdin_pipe_descriptor: int,
) -> None:
    """A piped stream that never becomes readable exits with a diagnostic."""
    buffer = _BufferedInput([b"unexpected payload"])
    monkeypatch.setattr(
        sys,
        "stdin",
        _DescriptorStdin(stdin_pipe_descriptor, buffer=buffer),
    )
    monkeypatch.setattr(sys, "argv", ["shim"])

    with pytest.raises(SystemExit) as exc:
        _create_invocation("shim", timeout=0.01)

    _assert_exit_code(exc, 1)
    assert buffer.read_calls == 0, "Buffered input was read before readiness"
    assert "IPC error: stdin read timed out after 0.01 seconds" in (
        capsys.readouterr().err
    ), "stdin timeout must produce the bounded stdin diagnostic"


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_reports_stdin_poll_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    stdin_pipe_descriptor: int,
) -> None:
    """Active poller failures use the bounded stdin diagnostic boundary."""
    buffer = _BufferedInput([b"must not be read"])

    class _FailingPoll:
        def __init__(self) -> None:
            """Distinguish the setup probe from the active readiness wait."""
            self.calls = 0

        def register(self, _fd: int, _events: int) -> None:
            """Accept the descriptor so failure occurs during the real wait."""

        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            """Succeed once for setup, then fail the active wait.

            Returns
            -------
            list[tuple[int, int]]
                No readiness events during the setup probe.

            Raises
            ------
            _InjectedOSError
                Simulated poll failure.
            """
            self.calls += 1
            if self.calls == 1:
                return []
            raise _InjectedOSError("hidden")

    monkeypatch.setattr(
        sys, "stdin", _DescriptorStdin(stdin_pipe_descriptor, buffer=buffer)
    )
    monkeypatch.setattr(capture.select, "poll", _FailingPoll)
    monkeypatch.setattr(sys, "argv", ["shim"])
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
    assert buffer.read_calls == 0, "stdin bytes were read after poller failure"
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

        def poll(self, timeout: int) -> list[tuple[int, int]]:
            """Record the bounded waits while keeping stdin unreadable.

            Returns
            -------
            list[tuple[int, int]]
                No readiness events.
            """
            self.timeouts.append(timeout)
            return []

    poller = _NeverReadablePoll()
    clock_values = iter([0.0, 0.0, 1e308])
    monkeypatch.setattr(sys, "stdin", _DescriptorStdin(stdin_pipe_descriptor))
    monkeypatch.setattr(capture.select, "poll", lambda: poller)
    monkeypatch.setattr(shim.time, "monotonic", lambda: next(clock_values))

    with pytest.raises(SystemExit):
        _create_invocation("shim", timeout=1e308)

    # The strategy probe is the leading zero-timeout wait; the bounded wait
    # that follows must be capped before conversion to milliseconds.
    assert poller.timeouts[1:] == [capture.MAX_POLL_TIMEOUT_MS], (
        "large timeouts must be capped before conversion to milliseconds"
    )
    assert "IPC error: stdin read timed out after 1e+308 seconds" in (
        capsys.readouterr().err
    ), "the capped poll path must retain the controlled timeout diagnostic"


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
    monkeypatch.delattr(capture.select, "poll")
    monkeypatch.setattr(capture.select, "select", fake_select)
    monkeypatch.setattr(capture.os, "read", lambda _fd, _size: next(chunks))
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

        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            """Report stdin readable so the test can deliver newline fragments.

            Returns
            -------
            list[tuple[int, int]]
                One readiness event for stdin.
            """
            return [(stdin_pipe_descriptor, capture.select.POLLIN)]

    chunks = iter([b"first\r", b"\nsecond\rthird", b""])
    monkeypatch.setattr(sys, "stdin", _DescriptorStdin(stdin_pipe_descriptor))
    monkeypatch.setattr(capture.select, "poll", _ReadablePoll)
    monkeypatch.setattr(capture.os, "read", lambda _fd, _size: next(chunks))
    monkeypatch.setattr(sys, "argv", ["shim"])

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "first\r\nsecond\rthird", (
        "bounded stdin reads must preserve POSIX newline sequences"
    )


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_preserves_buffered_stdin(
    monkeypatch: pytest.MonkeyPatch, stdin_pipe_descriptor_at_eof: int
) -> None:
    """Bytes already buffered by stdin are included in the invocation."""
    monkeypatch.setattr(
        sys,
        "stdin",
        _DescriptorStdin(
            stdin_pipe_descriptor_at_eof,
            buffer=_BufferedInput([b"buffered", b""]),
        ),
    )
    monkeypatch.setattr(sys, "argv", ["shim"])

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "buffered", "Buffered stdin bytes were lost"
    assert os.get_blocking(stdin_pipe_descriptor_at_eof), (
        "stdin blocking mode was not restored"
    )


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_checks_deadline_between_buffered_reads(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stdin_pipe_descriptor_at_eof: int,
) -> None:
    """Buffered input cannot extend the deadline past its configured bound."""
    clock = {"value": 0.0}
    buffer = _BufferedInput([b"buffered"], lambda: clock.__setitem__("value", 2.0))
    monkeypatch.setattr(
        sys,
        "stdin",
        _DescriptorStdin(stdin_pipe_descriptor_at_eof, buffer=buffer),
    )
    monkeypatch.setattr(sys, "argv", ["shim"])
    monkeypatch.setattr(shim.time, "monotonic", lambda: clock["value"])

    with pytest.raises(SystemExit) as exc:
        _create_invocation("shim", timeout=1.0)

    _assert_exit_code(exc, 1)
    assert buffer.read_calls == 1, "Buffered input bypassed the deadline"
    assert "IPC error: stdin read timed out after 1 seconds" in (
        capsys.readouterr().err
    ), "Buffered-input timeout must include the bounded stdin diagnostic"


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_does_not_change_inherited_stdin_mode(
    monkeypatch: pytest.MonkeyPatch, stdin_pipe_descriptor_at_eof: int
) -> None:
    """Reading piped stdin never changes the parent's shared file status flags."""
    monkeypatch.setattr(
        sys,
        "stdin",
        _DescriptorStdin(
            stdin_pipe_descriptor_at_eof,
            buffer=_BufferedInput([b"buffered", b""]),
        ),
    )
    monkeypatch.setattr(sys, "argv", ["shim"])

    blocking_mode_changes: list[tuple[object, ...]] = []

    def set_blocking(*args: object) -> None:
        """Record every attempt to change the inherited descriptor mode."""
        blocking_mode_changes.append(args)

    monkeypatch.setattr(capture.os, "set_blocking", set_blocking)

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "buffered", "Buffered stdin bytes were lost"
    assert blocking_mode_changes == [], "The shim changed the inherited descriptor mode"
    assert os.get_blocking(stdin_pipe_descriptor_at_eof), (
        "The parent stdin descriptor must remain blocking"
    )
