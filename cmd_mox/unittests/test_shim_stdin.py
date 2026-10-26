"""Tests for shim-level stdin capture and its failure boundary."""

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
    _InjectedRuntimeError,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path


def _stream_dependencies(
    stream: object,
    *,
    monotonic: cabc.Callable[[], float] = lambda: 0.0,
    duration: cabc.Callable[[], float] = lambda: 0.0,
) -> shim._InvocationDependencies:
    """Bind *stream* and the supplied clocks for one invocation.

    Returns
    -------
    shim._InvocationDependencies
        Dependencies that replace the process-backed defaults.
    """
    return shim._InvocationDependencies(
        stdin_stream=typ.cast("shim._StdinTextStream", stream),
        monotonic_clock=monotonic,
        duration_clock=duration,
    )


class _DescriptorTextStream(_DummyStdin):
    """Non-tty stdin double exposing a descriptor and decoder settings."""

    encoding = "utf-8"
    errors = "strict"

    def __init__(self, descriptor: int) -> None:
        """Expose *descriptor* to the capture strategies."""
        super().__init__("", is_tty=False)
        self._descriptor = descriptor

    def fileno(self) -> int:
        """Return the descriptor supplied to this double.

        Returns
        -------
        int
            The descriptor configured for the double.
        """
        return self._descriptor


# --- Invocation construction -------------------------------------------------


def test_create_invocation_skips_tty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """When stdin is a TTY, invocation payload should contain empty stdin."""
    dummy_stdin = _DummyStdin("ignored", is_tty=True)
    monkeypatch.setattr(sys, "stdin", dummy_stdin)
    monkeypatch.setattr(sys, "argv", ["shim", "--flag"])
    monkeypatch.setenv("EXTRA", "value")

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.command == "shim", "invocation command was not recorded"
    assert invocation.args == ["--flag"], "invocation arguments were not recorded"
    assert invocation.stdin == "", "TTY stdin must not be captured"
    assert dummy_stdin.read_calls == 0, "TTY stdin was read"
    assert invocation.env["EXTRA"] == "value", "environment was not copied"


def test_create_invocation_reads_stdin_when_not_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-TTY stdin should be captured into the invocation."""
    dummy_stdin = _DummyStdin("payload", is_tty=False)
    monkeypatch.setattr(sys, "stdin", dummy_stdin)
    monkeypatch.setattr(sys, "argv", ["shim"])

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "payload", "non-TTY stdin was not captured"
    assert dummy_stdin.read_calls == 1, "non-TTY stdin was not read exactly once"


def test_create_invocation_accepts_stdin_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invocation construction uses its injected stream instead of ``sys.stdin``."""
    stdin = _DummyStdin("injected payload", is_tty=False)

    class _UnexpectedSystemStdin:
        def isatty(self) -> typ.NoReturn:
            """Fail if invocation construction bypasses the injected stream."""
            pytest.fail("system stdin was queried despite injected dependencies")

    monkeypatch.setattr(sys, "argv", ["shim"])
    monkeypatch.setattr(sys, "stdin", _UnexpectedSystemStdin())

    invocation = _create_invocation(
        "shim",
        timeout=1.0,
        dependencies=_stream_dependencies(stdin),
    )

    assert invocation.stdin == "injected payload", (
        "invocation did not read from its injected stdin source"
    )
    assert stdin.read_calls == 1, "injected stdin source was not read exactly once"


def test_string_io_stdin_is_read_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    """In-memory stdin is captured without a worker or descriptor."""
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO("in-memory"))
    monkeypatch.setattr(sys, "argv", ["shim"])

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "in-memory", "StringIO stdin was not captured"


# --- Failure reporting ------------------------------------------------------


def test_create_invocation_observes_stdin_failure_without_error_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed stdin read emits bounded, correlated telemetry and diagnostics."""

    class _FailingStdin:
        def isatty(self) -> bool:
            """Report the non-interactive status that triggers a read.

            Returns
            -------
            bool
                ``False``, so stdin is captured.
            """
            return False

        def read(self) -> str:
            """Fail the read with text that must not leak.

            Raises
            ------
            OSError
                Always, carrying a sentinel message.
            """
            raise OSError("leak")

    monkeypatch.setattr(sys, "stdin", _FailingStdin())
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
        "stdin failure did not produce a bounded diagnostic"
    )
    assert "leak" not in diagnostic, "stdin exception details must not reach stderr"
    assert "leak" not in caplog.text, "stdin exception details must not reach logs"
    [event] = events
    assert event.operation == "ipc.shim.stdin_read", "Wrong stdin event operation"
    assert event.outcome == "read_failed", "Stdin failure outcome was not recorded"
    assert event.error_category == "OSError", "Wrong stdin error category"
    assert event.correlation_id is not None, "Stdin failure lost its correlation id"
    assert len(event.correlation_id) == 32, "Stdin correlation id is not opaque"
    assert event.duration_ms is not None, "Stdin failure lost its duration"
    assert event.duration_ms >= 0, "Stdin failure duration is negative"
    counts_after = _observability.counter_snapshot()
    assert (
        counts_after.get(event.key(), 0) == counters_before.get(event.key(), 0) + 1
    ), "Stdin failure did not increment its bounded event counter"


@pytest.mark.parametrize("fallback", ["windows", "missing-fd", "unpollable"])
@pytest.mark.parametrize(
    "error",
    [
        pytest.param(OSError("read failed"), id="oserror"),
        pytest.param(
            UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
            id="unicode-error",
        ),
        pytest.param(ValueError("read failed"), id="value-error"),
        pytest.param(RuntimeError("read failed"), id="runtime-error"),
    ],
)
def test_direct_stdin_fallback_routes_read_errors(
    monkeypatch: pytest.MonkeyPatch, fallback: str, error: Exception
) -> None:
    """Every direct-read fallback reports supported failures as IPC errors."""

    class _FailingStdin:
        encoding = "utf-8"
        errors = "strict"

        def isatty(self) -> bool:
            """Report the non-interactive status shared by the fallbacks.

            Returns
            -------
            bool
                ``False``, so stdin is captured.
            """
            return False

        def read(self) -> str:
            """Raise the selected error from the direct text-read path."""
            raise error

    class _UnpollableStdin(_FailingStdin):
        def fileno(self) -> int:
            """Provide the descriptor used by the unpollable fallback case.

            Returns
            -------
            int
                A synthetic descriptor for the fallback selection.
            """
            return 123

    if fallback == "windows":
        monkeypatch.setattr(capture.path_utils, "IS_WINDOWS", True)
        monkeypatch.setattr(capture, "_stdin_handle", lambda _stream: None)
        stdin: object = _FailingStdin()
    elif fallback == "missing-fd":
        stdin = _FailingStdin()
    else:
        stdin = _UnpollableStdin()
        monkeypatch.setattr(capture, "_create_stdin_poller", lambda _descriptor: None)

    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "argv", ["shim"])

    with pytest.raises(SystemExit) as exc:
        _create_invocation("shim", timeout=1.0)

    _assert_exit_code(exc, 1)


def test_worker_start_failure_uses_controlled_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Failure to start the bounded reader does not escape as a traceback."""

    class _FailingThread:
        def __init__(self, **_kwargs: object) -> None:
            """Accept thread construction so the injected start failure is reached."""

        def start(self) -> typ.NoReturn:
            """Simulate failure to start the bounded stdin worker.

            Raises
            ------
            _InjectedRuntimeError
                Simulated worker startup failure.
            """
            raise _InjectedRuntimeError

    class _StreamWithoutDescriptor:
        def isatty(self) -> bool:
            """Report the non-interactive status that selects a bounded read.

            Returns
            -------
            bool
                ``False``, so stdin is captured.
            """
            return False

        def read(self) -> str:
            """Return a value that cannot be reached after a start failure.

            Returns
            -------
            str
                A sentinel that would reveal a bypassed worker.
            """
            return "unreachable"

    monkeypatch.setattr(capture.threading, "Thread", _FailingThread)
    monkeypatch.setattr(sys, "stdin", _StreamWithoutDescriptor())
    monkeypatch.setattr(sys, "argv", ["shim"])

    with pytest.raises(SystemExit) as exc:
        _create_invocation("shim", timeout=1.0)

    _assert_exit_code(exc, 1)
    assert "IPC error: stdin read failed" in capsys.readouterr().err, (
        "worker start failure did not produce a controlled diagnostic"
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


# --- Regular-file strategy --------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_reads_regular_file_stdin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Regular-file stdin is read without changing its text semantics."""
    path = tmp_path / "input.txt"
    path.write_text("regular input", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["shim"])

    with path.open(encoding="utf-8") as stdin:
        monkeypatch.setattr(sys, "stdin", stdin)
        invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "regular input", "Regular-file stdin was not captured"


@pytest.mark.skipif(os.name == "nt", reason="regular-file worker is POSIX-specific")
def test_create_invocation_bounds_stalled_regular_file_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """A blocked raw regular-file read still observes the invocation deadline."""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def stalled_read(_descriptor: int, _size: int) -> bytes:
        """Hold the raw read until the test releases the worker.

        Returns
        -------
        bytes
            EOF once the test releases the blocked worker.
        """
        started.set()
        release.wait(5.0)
        finished.set()
        return b""

    path = tmp_path / "stalled.txt"
    path.write_text("never read", encoding="utf-8")
    monkeypatch.setattr(capture.os, "read", stalled_read)
    monkeypatch.setattr(sys, "argv", ["shim"])

    with path.open(encoding="utf-8") as regular_file:
        monkeypatch.setattr(sys, "stdin", _DescriptorTextStream(regular_file.fileno()))
        try:
            with pytest.raises(SystemExit) as exc:
                _create_invocation("shim", timeout=0.05)
        finally:
            release.set()

        _assert_exit_code(exc, 1)
        assert started.is_set(), "Regular-file reader did not start"
        assert "IPC error: stdin read timed out after 0.05 seconds" in (
            capsys.readouterr().err
        ), "Stalled regular-file input must include the bounded diagnostic"
        assert finished.wait(timeout=5.0), "Timed-out stdin worker did not stop"


@pytest.mark.skipif(os.name == "nt", reason="regular-file worker is POSIX-specific")
def test_regular_stdin_reads_descriptor_without_buffered_stream(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regular-file worker uses raw reads and preserves text decoding."""
    chunks = iter([b"\xe2\x82", b"\xac\r\n", b""])
    descriptors: list[int] = []
    read_calls = 0

    class _RegularStdin(_DescriptorTextStream):
        def read(self) -> str:
            """Count buffered reads that must not occur on this path.

            Returns
            -------
            str
                A sentinel value that identifies the buffered path.
            """
            nonlocal read_calls
            read_calls += 1
            return "buffered text"

    def read_raw(descriptor: int, _size: int) -> bytes:
        """Capture the file descriptor and provide the next raw chunk.

        Returns
        -------
        bytes
            The next scripted raw input chunk.
        """
        descriptors.append(descriptor)
        return next(chunks)

    path = tmp_path / "regular.txt"
    path.write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(capture.os, "read", read_raw)
    monkeypatch.setattr(sys, "argv", ["shim"])

    with path.open(encoding="utf-8") as regular_file:
        stdin = _RegularStdin(regular_file.fileno())
        monkeypatch.setattr(sys, "stdin", stdin)
        invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "\N{EURO SIGN}\r\n", (
        "Raw stdin chunks did not preserve the original newline sequence"
    )
    assert descriptors, "Regular stdin reader did not call os.read"
    assert all(descriptor == stdin.fileno() for descriptor in descriptors), (
        "Regular stdin reads did not use its file descriptor"
    )
    assert read_calls == 0, "The buffered stdin wrapper was read"


@pytest.mark.skipif(os.name == "nt", reason="regular-file worker is POSIX-specific")
def test_regular_stdin_read_error_uses_controlled_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Raw regular-file read errors return a diagnostic to the shim."""

    def fail_read(_descriptor: int, _size: int) -> typ.NoReturn:
        """Raise an I/O failure from the raw regular-file read.

        Raises
        ------
        _InjectedOSError
            Simulated descriptor read failure.
        """
        raise _InjectedOSError

    path = tmp_path / "failing.txt"
    path.write_text("ignored", encoding="utf-8")
    monkeypatch.setattr(capture.os, "read", fail_read)
    monkeypatch.setattr(sys, "argv", ["shim"])

    with path.open(encoding="utf-8") as regular_file:
        monkeypatch.setattr(sys, "stdin", _DescriptorTextStream(regular_file.fileno()))
        with pytest.raises(SystemExit) as exc:
            _create_invocation("shim", timeout=1.0)

    _assert_exit_code(exc, 1)
    assert "IPC error: stdin read failed" in capsys.readouterr().err, (
        "regular-file read failure did not produce a controlled diagnostic"
    )
