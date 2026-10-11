"""Tests for bounded shim stdin capture."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import typing as typ

import pytest

from cmd_mox import shim
from cmd_mox.shim import _create_invocation
from cmd_mox.unittests._shim_test_support import (
    _assert_exit_code,
    _BufferedInput,
    _DescriptorStdin,
    _DummyStdin,
    _InjectedOSError,
)


def test_create_invocation_skips_tty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """When stdin is a TTY, invocation payload should contain empty stdin."""
    dummy_stdin = _DummyStdin("ignored", is_tty=True)
    monkeypatch.setattr(sys, "stdin", dummy_stdin)
    monkeypatch.setattr(sys, "argv", ["shim", "--flag"])
    monkeypatch.setenv("EXTRA", "value")

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.command == "shim"
    assert invocation.args == ["--flag"]
    assert invocation.stdin == ""
    assert dummy_stdin.read_calls == 0
    assert invocation.env["EXTRA"] == "value"


def test_create_invocation_reads_stdin_when_not_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-TTY stdin should be captured into the invocation."""
    dummy_stdin = _DummyStdin("payload", is_tty=False)
    monkeypatch.setattr(sys, "stdin", dummy_stdin)
    monkeypatch.setattr(sys, "argv", ["shim"])

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "payload"
    assert dummy_stdin.read_calls == 1


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
    """Direct-read fallbacks report supported read failures unchanged."""

    class _FailingStdin:
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
        monkeypatch.setattr(shim._shim_stdin.path_utils, "IS_WINDOWS", True)
        stdin = _FailingStdin()
    elif fallback == "missing-fd":
        stdin = _FailingStdin()
    else:
        stdin = _UnpollableStdin()
        monkeypatch.setattr(
            shim._shim_stdin, "_create_stdin_poller", lambda _descriptor: None
        )

    monkeypatch.setattr(sys, "stdin", stdin)
    reported: list[Exception] = []

    class _ErrorReportedError(Exception):
        pass

    def record_error(exc: Exception) -> typ.NoReturn:
        """Record the original error before stopping the callback path.

        Raises
        ------
        _ErrorReportedError
            Stop the shim callback after recording the original exception.
        """
        reported.append(exc)
        raise _ErrorReportedError

    with pytest.raises(_ErrorReportedError):
        shim._shim_stdin._read_stdin_until_eof(
            1.0, deadline=None, on_error=record_error
        )

    assert len(reported) == 1, "Direct-read error was not reported exactly once"
    assert reported[0] is error, "Direct-read error identity changed before reporting"


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
    stdin = _DescriptorStdin(stdin_pipe_descriptor_at_eof, buffer=buffer)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "argv", ["shim"])
    monkeypatch.setattr(shim._shim_stdin.time, "monotonic", lambda: clock["value"])

    with pytest.raises(SystemExit) as exc:
        _create_invocation("shim", timeout=1.0)

    _assert_exit_code(exc, 1)
    assert buffer.read_calls == 1, "Buffered input bypassed the deadline"
    assert "IPC error: timed out reading stdin" in capsys.readouterr().err, (
        "Buffered-input timeout must include an IPC diagnostic"
    )
    assert os.get_blocking(stdin_pipe_descriptor_at_eof), (
        "stdin blocking mode was not restored"
    )


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

    original_set_blocking = os.set_blocking
    blocking_mode_changes: list[bool] = []

    def set_blocking(
        descriptor: int,
        blocking: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - mirrors os.set_blocking
    ) -> None:
        """Record any attempt to change the inherited descriptor mode."""
        blocking_mode_changes.append(blocking)
        original_set_blocking(descriptor, blocking)

    monkeypatch.setattr(shim._shim_stdin.os, "set_blocking", set_blocking)

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "buffered", "Buffered stdin bytes were lost"
    assert blocking_mode_changes == [], "The shim changed the inherited descriptor mode"
    assert os.get_blocking(stdin_pipe_descriptor_at_eof), (
        "The parent stdin descriptor must remain blocking"
    )


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_reads_regular_file_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regular-file stdin is read without changing its text semantics."""
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stdin:
        stdin.write("regular input")
        stdin.seek(0)
        monkeypatch.setattr(sys, "stdin", stdin)
        monkeypatch.setattr(sys, "argv", ["shim"])

        invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "regular input", "Regular-file stdin was not captured"


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_bounds_stalled_regular_file_read(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A blocked raw regular-file read still observes the invocation deadline."""
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class _StalledRegularStdin(_DummyStdin):
        def __init__(self, descriptor: int) -> None:
            """Expose the regular-file descriptor through the stdin double."""
            super().__init__("", is_tty=False)
            self._descriptor = descriptor

        def fileno(self) -> int:
            """Return the descriptor whose raw read is stalled by the test.

            Returns
            -------
            int
                The regular-file descriptor under test.
            """
            return self._descriptor

    def stalled_read(_descriptor: int, _size: int) -> bytes:
        """Hold the raw read until the test releases the worker.

        Returns
        -------
        bytes
            EOF once the test releases the blocked worker.
        """
        started.set()
        release.wait()
        finished.set()
        return b""

    with tempfile.TemporaryFile() as regular_file:
        monkeypatch.setattr(sys, "stdin", _StalledRegularStdin(regular_file.fileno()))
        monkeypatch.setattr(shim._shim_stdin.os, "read", stalled_read)
        monkeypatch.setattr(sys, "argv", ["shim"])

        try:
            with pytest.raises(SystemExit) as exc:
                _create_invocation("shim", timeout=0.05)

            _assert_exit_code(exc, 1)
            assert started.wait(timeout=1.0), "Regular-file reader did not start"
            assert "IPC error: timed out reading stdin" in capsys.readouterr().err, (
                "Stalled regular-file input must include an IPC diagnostic"
            )
        finally:
            release.set()

        assert finished.wait(timeout=1.0), "Timed-out stdin worker did not stop"


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
    monkeypatch.setattr(
        shim._shim_stdin.time, "monotonic", lambda: next(monotonic_values)
    )

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
def test_create_invocation_falls_back_to_direct_read_when_unpollable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported descriptors retain the established direct-read behaviour."""

    class _UnpollableStdin(_DummyStdin):
        def fileno(self) -> int:
            """Provide the descriptor passed to the mocked pollers.

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
            Poll setup failure used to select direct-read fallback.
        """
        raise _InjectedOSError

    def fail_select(*_args: object) -> typ.NoReturn:
        """Simulate an unavailable select implementation.

        Raises
        ------
        _InjectedOSError
            Select setup failure used to select direct-read fallback.
        """
        raise _InjectedOSError

    stdin = _UnpollableStdin("payload", is_tty=False)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(shim._shim_stdin.select, "poll", fail_poll)
    monkeypatch.setattr(shim._shim_stdin.select, "select", fail_select)

    invocation = _create_invocation("shim", timeout=1.0)

    assert invocation.stdin == "payload"
    assert stdin.read_calls == 1


def test_create_invocation_keeps_windows_stdin_read_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows continues to read standard input directly."""
    stdin = _DummyStdin("payload", is_tty=False)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(shim._shim_stdin.path_utils, "IS_WINDOWS", True)

    assert (
        shim._shim_stdin._read_stdin_until_eof(
            1.0,
            deadline=None,
            on_error=shim._exit_ipc_error,
        )
        == "payload"
    )
    assert stdin.read_calls == 1


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_create_invocation_normalises_piped_newlines(
    monkeypatch: pytest.MonkeyPatch, stdin_pipe_descriptor: int
) -> None:
    """Piped text preserves TextIO newline translation across byte chunks."""

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

    assert invocation.stdin == "first\nsecond\nthird", (
        "bounded stdin reads must preserve universal-newline translation"
    )
