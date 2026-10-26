"""Tests for shim-level stdin capture in invocation construction.

These cover how ``_create_invocation`` populates :class:`Invocation` from its
stdin stream, and how every stdin read failure is reported as a bounded
diagnostic rather than an escaping traceback.
"""

from __future__ import annotations

import sys
import typing as typ

import pytest

from cmd_mox import _stdin_capture as capture
from cmd_mox import shim
from cmd_mox.ipc import _observability
from cmd_mox.shim import _create_invocation
from cmd_mox.unittests._shim_test_support import (
    _assert_exit_code,
    _DummyStdin,
    _InjectedRuntimeError,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc


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
