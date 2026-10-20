"""Tests for top-level shim stdin capture and fallback behaviour."""

from __future__ import annotations

import os
import sys
import typing as typ

import pytest

from cmd_mox import shim
from cmd_mox.ipc import _observability
from cmd_mox.shim import _create_invocation
from cmd_mox.unittests._shim_test_support import (
    _assert_exit_code,
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
    """Invocation construction uses its injected stdin source and clocks."""

    class _InjectedStdin(_DummyStdin):
        @property
        def encoding(self) -> str:
            """Report the encoding required by the stdin stream protocol."""
            return "utf-8"

        @property
        def errors(self) -> str:
            """Report the decoder error policy required by the stream protocol."""
            return "strict"

        def fileno(self) -> int:
            """Select the direct-read fallback for this injected stream.

            Raises
            ------
            ValueError
                This synthetic stream has no file descriptor.
            """
            raise ValueError

    stdin = _InjectedStdin("injected payload", is_tty=False)
    source = shim._shim_stdin._StdinSource(
        stream=stdin,
        create_poller=lambda _descriptor: None,
        read_descriptor=os.read,
        stat_descriptor=os.fstat,
    )
    dependencies = shim._InvocationDependencies(
        stdin_read=shim._shim_stdin._StdinReadDependencies(
            source=source,
            clock=lambda: 0.0,
        ),
        duration_clock=lambda: 1.0,
    )

    class _UnexpectedSystemStdin:
        def isatty(self) -> typ.NoReturn:
            """Fail if invocation construction bypasses the injected source."""
            pytest.fail("system stdin was queried despite injected dependencies")

    monkeypatch.setattr(sys, "argv", ["shim"])
    monkeypatch.setattr(sys, "stdin", _UnexpectedSystemStdin())

    invocation = _create_invocation("shim", timeout=1.0, dependencies=dependencies)

    assert invocation.stdin == "injected payload", (
        "invocation did not read from its injected stdin source"
    )
    assert stdin.read_calls == 1, "injected stdin source was not read exactly once"


def test_create_invocation_observes_stdin_failure_without_error_text(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed stdin read emits bounded, correlated telemetry and diagnostics."""

    class _FailingStdin:
        def isatty(self) -> bool:
            return False

        def read(self) -> typ.NoReturn:
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
            1.0,
            deadline=None,
            on_error=record_error,
            dependencies=shim._shim_stdin._StdinReadDependencies(
                source=shim._shim_stdin._system_stdin_source(),
                clock=lambda: 0.0,
            ),
        )

    assert len(reported) == 1, "Direct-read error was not reported exactly once"
    assert reported[0] is error, "Direct-read error identity changed before reporting"


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

    assert invocation.stdin == "payload", "direct-read fallback lost stdin payload"
    assert stdin.read_calls == 1, "direct-read fallback must read stdin exactly once"


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
            dependencies=shim._shim_stdin._StdinReadDependencies(
                source=shim._shim_stdin._system_stdin_source(),
                clock=lambda: 0.0,
            ),
        )
        == "payload"
    ), "Windows stdin direct read did not preserve its payload"
    assert stdin.read_calls == 1, "Windows stdin must be read exactly once"
