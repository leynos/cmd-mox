"""Tests for shim invocation, passthrough, and response handling."""

from __future__ import annotations

import math
import os
import subprocess
import sys
import typing as typ

import pytest

from cmd_mox import shim
from cmd_mox.ipc import (
    Invocation,
    PassthroughRequest,
    PassthroughResult,
    Response,
    _observability,
)
from cmd_mox.shim import _execute_invocation, _write_response
from cmd_mox.unittests._shim_test_support import (
    _assert_exit_code,
)


def test_execute_invocation_returns_response_without_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regular IPC responses should be returned directly."""
    invocation = Invocation(
        command="cmd", args=[], stdin="", env={}, invocation_id="abc"
    )
    expected = Response(stdout="ok", stderr="", exit_code=0)

    calls: dict[str, typ.Any] = {}

    def fake_invoke(
        inv: Invocation, timeout: float, *, deadline: float | None = None
    ) -> Response:
        """Capture invocation arguments and return the expected response.

        Returns
        -------
        Response
            The regular IPC response expected by the test.
        """
        calls["invocation"] = inv
        calls["timeout"] = timeout
        calls["deadline"] = deadline
        return expected

    monkeypatch.setattr(shim, "invoke_server", fake_invoke)

    def fail_passthrough(*_args: object, **_kwargs: object) -> typ.NoReturn:
        """Fail if a regular invocation unexpectedly enters passthrough.

        Returns
        -------
        NoReturn
            Never returns; ``pytest.fail`` fails the test if called.
        """
        return pytest.fail("passthrough handler should not run")

    monkeypatch.setattr(
        shim,
        "_handle_passthrough",
        fail_passthrough,  # ty misreads @_with_exception
    )

    result = _execute_invocation(invocation, timeout=1.5)

    assert result is expected, "regular invocation response was not returned"
    assert calls["invocation"] is invocation, "IPC received a different invocation"
    assert math.isclose(calls["timeout"], 1.5), "configured IPC timeout was changed"
    assert calls["deadline"] is None, "No shared deadline should be synthesized"


def test_execute_invocation_processes_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passthrough responses should be resolved through the passthrough handler."""
    invocation = Invocation(
        command="cmd", args=[], stdin="", env={}, invocation_id="abc"
    )
    directive = PassthroughRequest(
        invocation_id="abc",
        lookup_path="/bin",
        extra_env={},
        timeout=2.0,
    )
    intermediate = Response(passthrough=directive)
    final = Response(stdout="done", stderr="", exit_code=0)

    monkeypatch.setattr(shim, "invoke_server", lambda *args, **kwargs: intermediate)

    def fake_passthrough(
        inv: Invocation,
        resp: Response,
        timeout: float,
        *,
        deadline: float | None = None,
    ) -> Response:
        """Check forwarded passthrough arguments and return the final response.

        Returns
        -------
        Response
            The final response expected from passthrough handling.
        """
        assert inv is invocation, "passthrough received a different invocation"
        assert resp is intermediate, "passthrough received a different response"
        assert math.isclose(timeout, 2.0), "passthrough timeout was changed"
        assert deadline is None, "an absent request deadline should remain absent"
        return final

    monkeypatch.setattr(shim, "_handle_passthrough", fake_passthrough)

    result = _execute_invocation(invocation, timeout=2.0)

    assert result is final, "passthrough result was not returned"


def test_execute_invocation_shares_deadline_with_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passthrough reporting receives only the original request's remainder."""
    invocation = Invocation(
        command="cmd", args=[], stdin="", env={}, invocation_id="abc"
    )
    directive = PassthroughRequest(
        invocation_id="abc",
        lookup_path="/bin",
        extra_env={},
        timeout=2.0,
    )
    intermediate = Response(passthrough=directive)
    final = Response(stdout="done", stderr="", exit_code=0)
    now = {"value": 100.0}
    timeouts: list[float] = []
    deadlines: list[float | None] = []

    monkeypatch.setattr(shim.time, "monotonic", lambda: now["value"])

    def fake_invoke(
        _inv: Invocation, timeout: float, *, deadline: float | None = None
    ) -> Response:
        """Record the initial IPC budget and advance the fake clock.

        Returns
        -------
        Response
            The intermediate response that requests passthrough handling.
        """
        timeouts.append(timeout)
        deadlines.append(deadline)
        now["value"] = 100.6
        return intermediate

    monkeypatch.setattr(shim, "invoke_server", fake_invoke)
    monkeypatch.setattr(shim, "_run_real_command", lambda *_args: Response(exit_code=0))

    def fake_report(
        _result: PassthroughResult,
        timeout: float,
        *,
        deadline: float | None = None,
    ) -> Response:
        """Record the remaining budget passed to passthrough reporting.

        Returns
        -------
        Response
            The completed response returned by passthrough reporting.
        """
        timeouts.append(timeout)
        deadlines.append(deadline)
        return final

    monkeypatch.setattr(shim, "report_passthrough_result", fake_report)

    result = _execute_invocation(invocation, timeout=1.0, deadline=101.0)

    assert result is final, "passthrough report response was not returned"
    assert timeouts == pytest.approx([1.0, 0.4]), (
        "passthrough reporting did not receive the original deadline's remainder"
    )
    assert deadlines == [101.0, 101.0], "The absolute deadline must span both IPC calls"


def test_timeout_remaining_without_deadline_returns_configured_timeout() -> None:
    """No shared deadline preserves the cooperative timeout value."""
    assert shim._timeout_remaining(2.5, None) == pytest.approx(2.5), (
        "A missing deadline must leave the configured timeout unchanged"
    )


def _assert_passthrough_expiry_event(
    events: list[_observability.IPCEvent],
    finished_at: float,
    counters_before: dict[_observability.EventKey, int],
) -> None:
    """Verify the single bounded event and its command-inclusive duration."""
    [event] = events
    assert event.operation == "ipc.shim.passthrough_report", "Wrong expiry operation"
    assert event.outcome == "deadline_expired", "Wrong expiry outcome"
    assert event.error_category == "TimeoutError", "Wrong expiry error category"
    assert event.transport == "unix", "Expiry lost its transport"
    assert event.correlation_id == "abc", "Expiry lost its validated correlation id"
    assert event.duration_ms == pytest.approx((finished_at - 100.0) * 1000), (
        "Expiry duration must include the time spent running the command"
    )
    counts_after = _observability.counter_snapshot()
    assert (
        counts_after.get(event.key(), 0) == counters_before.get(event.key(), 0) + 1
    ), "Expiry must increment exactly one bounded event counter"


@pytest.mark.parametrize(
    "case",
    [(101.0, 0), (101.25, 0), (101.0, 7), (101.25, 7)],
    ids=["successful-at", "successful-after", "failed-at", "failed-after"],
)
def test_passthrough_deadline_expiry_emits_bounded_event(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
    case: tuple[float, int],
) -> None:
    """Expiry before dispatch records one event and preserves command output."""
    finished_at, exit_code = case
    invocation = Invocation(
        command="private-command", args=[], stdin="", env={}, invocation_id="abc"
    )
    directive = PassthroughRequest(
        invocation_id="abc", lookup_path="/bin", extra_env={}, timeout=2.0
    )
    now = {"value": 100.0}
    monkeypatch.setattr(shim.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(shim.time, "perf_counter", lambda: now["value"])
    monkeypatch.setattr(shim.path_utils, "IS_WINDOWS", False)

    def finish_command(*_args: object) -> Response:
        """Return command output after the shared reporting deadline expires.

        Returns
        -------
        Response
            The completed command's output and configured status.
        """
        now["value"] = finished_at
        return Response(
            stdout="private-output", stderr="private-error", exit_code=exit_code
        )

    monkeypatch.setattr(shim, "_run_real_command", finish_command)

    def unexpected_report(*_args: object, **_kwargs: object) -> typ.NoReturn:
        """Reject any report attempted after its original deadline.

        Returns
        -------
        NoReturn
            Never returns because dispatch after expiry is a test failure.
        """
        return pytest.fail("expired passthrough report must not be dispatched")

    monkeypatch.setattr(shim, "report_passthrough_result", unexpected_report)
    counters_before = _observability.counter_snapshot()
    with (
        caplog.at_level("INFO", logger="cmd_mox.shim"),
        _observability.capture_events() as events,
        pytest.raises(SystemExit) as exc,
    ):
        shim._handle_passthrough(
            invocation, Response(passthrough=directive), timeout=1.0, deadline=101.0
        )

    _assert_exit_code(exc, exit_code or 1)
    captured = capsys.readouterr()
    assert captured.out == "private-output", "Expiry discarded passthrough stdout"
    assert (
        captured.err == "private-errorIPC error: timed out reporting passthrough\n"
    ), "Expiry discarded stderr or changed the controlled timeout diagnostic"
    _assert_passthrough_expiry_event(events, finished_at, counters_before)
    assert not any(
        text in caplog.text
        for text in (
            "private-command",
            "private-output",
            "private-error",
            "IPC operation timed out",
        )
    ), "Passthrough command or exception content leaked into logs"


def test_timeout_remaining_keeps_shim_timeout_diagnostic() -> None:
    """An expired shared deadline retains the shim's established message."""
    with pytest.raises(TimeoutError, match="IPC operation timed out"):
        shim._timeout_remaining(2.5, 0.0)


def test_invocation_deadline_expiry_emits_bounded_event(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Expiry before initial dispatch records one bounded failure."""
    invocation = Invocation(
        command="private-command", args=[], stdin="", env={}, invocation_id="abc"
    )
    monkeypatch.setattr(shim.time, "monotonic", lambda: 101.0)
    ticks = iter((200.0, 200.25))
    monkeypatch.setattr(shim.time, "perf_counter", lambda: next(ticks))
    monkeypatch.setattr(shim.path_utils, "IS_WINDOWS", False)

    def unexpected_request(*_args: object, **_kwargs: object) -> typ.NoReturn:
        """Reject dispatch after the shared deadline expires.

        Returns
        -------
        NoReturn
            Never returns because dispatch after expiry is a test failure.
        """
        return pytest.fail("expired invocation must not be dispatched")

    monkeypatch.setattr(shim, "invoke_server", unexpected_request)
    counters_before = _observability.counter_snapshot()
    with (
        _observability.capture_events() as events,
        pytest.raises(SystemExit) as exc,
    ):
        _execute_invocation(invocation, timeout=1.0, deadline=101.0)

    _assert_exit_code(exc, 1)
    assert capsys.readouterr().err == "IPC error: IPC operation timed out\n", (
        "Expiry changed the controlled IPC diagnostic"
    )
    [event] = events
    assert event.operation == "ipc.shim.ipc_request", "Wrong expiry operation"
    assert event.outcome == "deadline_expired", "Wrong expiry outcome"
    assert event.error_category == "TimeoutError", "Wrong expiry error category"
    assert event.transport == "unix", "Expiry lost its transport"
    assert event.correlation_id == "abc", "Expiry lost its invocation id"
    assert event.duration_ms == pytest.approx(250.0), (
        "Expiry duration must use the performance clock"
    )
    counts_after = _observability.counter_snapshot()
    assert (
        counts_after.get(event.key(), 0) == counters_before.get(event.key(), 0) + 1
    ), "Expiry must increment exactly one bounded event counter"


def test_execute_invocation_surfaces_ipc_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exceptions raised by IPC helpers should trigger a controlled exit."""
    invocation = Invocation(
        command="cmd", args=[], stdin="", env={}, invocation_id="abc"
    )

    def raise_error(*_: object, **__: object) -> typ.NoReturn:
        """Simulate an IPC transport error for the controlled-exit check.

        Raises
        ------
        OSError
            The transport failure handled by the shim.
        """
        raise OSError("boom")

    monkeypatch.setattr(shim, "invoke_server", raise_error)

    with (
        _observability.capture_events() as events,
        pytest.raises(SystemExit) as exc,
    ):
        _execute_invocation(invocation, timeout=1.0)

    _assert_exit_code(exc, 1)
    assert events == [], "Dispatched failures must not add a duplicate shim event"
    diagnostic = capsys.readouterr().err
    assert "IPC error: IPC operation failed" in diagnostic, (
        "IPC failure did not produce a bounded diagnostic"
    )
    assert "boom" not in diagnostic, "Exception messages must not reach stderr"


def test_passthrough_report_failure_preserves_nonzero_exit_code(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reporting a failed passthrough exits through its determined status."""
    invocation = Invocation(
        command="cmd", args=[], stdin="", env={}, invocation_id="abc"
    )
    directive = PassthroughRequest(
        invocation_id="abc", lookup_path="/bin", extra_env={}, timeout=1.0
    )
    response = Response(passthrough=directive)

    monkeypatch.setattr(
        shim,
        "_run_real_command",
        lambda *_args: Response(
            stdout="command output", stderr="command error", exit_code=2
        ),
    )

    def raise_error(*_: object, **__: object) -> typ.NoReturn:
        """Simulate a server that fails to acknowledge the passthrough result.

        Raises
        ------
        TimeoutError
            The missing acknowledgement reported by the shim.
        """
        msg = "server did not acknowledge passthrough"
        raise TimeoutError(msg)

    monkeypatch.setattr(shim, "report_passthrough_result", raise_error)

    with (
        _observability.capture_events() as events,
        pytest.raises(SystemExit) as exc,
    ):
        shim._handle_passthrough(invocation, response, timeout=1.0)

    _assert_exit_code(exc, 2)
    assert not events, "Client-path failures must not gain a duplicate shim event"
    captured = capsys.readouterr()
    assert captured.out == "command output", (
        "A report failure must not discard passthrough stdout"
    )
    assert "command error" in captured.err, (
        "A report failure must not discard passthrough stderr"
    )
    assert "IPC error: timed out reporting passthrough" in captured.err, (
        "passthrough report timeout must have a bounded diagnostic"
    )
    assert "server did not acknowledge" not in captured.err, (
        "passthrough exception details must not reach stderr"
    )


def test_passthrough_report_failure_survives_stdout_write_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed output write must not hide the passthrough IPC failure."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    directive = PassthroughRequest(
        invocation_id="abc", lookup_path="/bin", extra_env={}, timeout=1.0
    )
    monkeypatch.setattr(
        shim,
        "_run_real_command",
        lambda *_args: Response(stdout="command output", exit_code=2),
    )

    def raise_error(*_: object, **__: object) -> typ.NoReturn:
        """Simulate a passthrough timeout before stdout also fails.

        Raises
        ------
        TimeoutError
            The timeout whose diagnostic must be preserved.
        """
        raise TimeoutError("timeout")

    class _ClosedWriter:
        def write(self, _text: str) -> typ.NoReturn:
            """Simulate stdout closing while the response is written.

            Raises
            ------
            BrokenPipeError
                Indicates that the output stream is closed.
            """
            raise BrokenPipeError("closed")

    monkeypatch.setattr(shim, "report_passthrough_result", raise_error)
    monkeypatch.setattr(sys, "stdout", _ClosedWriter())

    with pytest.raises(SystemExit) as exc:
        shim._handle_passthrough(
            invocation,
            Response(passthrough=directive),
            timeout=1.0,
        )

    _assert_exit_code(exc, 2)
    stderr = capsys.readouterr().err
    assert "IPC error: timed out reporting passthrough" in stderr, (
        "passthrough IPC failure was hidden by the output write failure"
    )


def test_write_response_updates_environment_and_streams(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Writing a response should forward IO and propagate exit status."""
    monkeypatch.setenv("EXISTING", "1")
    response = Response(stdout="out", stderr="err", exit_code=3, env={"NEW": "value"})

    with pytest.raises(SystemExit) as exc:
        _write_response(response)

    _assert_exit_code(exc, 3)
    captured = capsys.readouterr()
    assert captured.out == "out", "response stdout was not written"
    assert captured.err == "err", "response stderr was not written"
    assert os.environ["NEW"] == "value", "response environment was not applied"


def test_write_response_handles_closed_stdout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A closed output stream produces a controlled IPC failure."""

    class _ClosedWriter:
        def write(self, _text: str) -> typ.NoReturn:
            """Simulate stdout closing while the response is written.

            Raises
            ------
            BrokenPipeError
                Indicates that the output stream is closed.
            """
            msg = "closed stdout"
            raise BrokenPipeError(msg)

    monkeypatch.setattr(sys, "stdout", _ClosedWriter())
    counters_before = _observability.counter_snapshot()

    with (
        caplog.at_level("INFO", logger="cmd_mox.shim"),
        _observability.capture_events() as events,
        pytest.raises(SystemExit) as exc,
    ):
        _write_response(
            Response(stdout="out", stderr="server diagnostic", exit_code=3),
            correlation_id="abc",
        )

    _assert_exit_code(exc, 3)
    stderr = capsys.readouterr().err
    assert "server diagnostic" in stderr, (
        "stdout failure must not discard the response stderr"
    )
    assert "IPC error: response stream write failed" in stderr, (
        "closed output streams must produce a bounded IPC diagnostic"
    )
    assert "closed stdout" not in stderr, "Exception details must not reach stderr"
    assert "closed stdout" not in caplog.text, "Exception details must not reach logs"
    [event] = events
    assert event.operation == "ipc.shim.response_stream", "Wrong event operation"
    assert event.outcome == "write_failed", "Write failure outcome was not recorded"
    assert event.error_category == "BrokenPipeError", "Wrong write error category"
    assert event.correlation_id == "abc", "Response failure lost its correlation id"
    assert event.duration_ms is not None, "Write failure lost its duration"
    assert event.duration_ms >= 0, "Write failure duration is negative"
    counts_after = _observability.counter_snapshot()
    assert (
        counts_after.get(event.key(), 0) == counters_before.get(event.key(), 0) + 1
    ), "Response write failure did not increment its bounded event counter"


def test_write_response_handles_stdout_flush_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A deferred output failure keeps the response exit code."""

    class _FlushFailure:
        def write(self, text: str) -> int:
            """Accept response text before the flush failure is exercised.

            Returns
            -------
            int
                The number of characters accepted by the fake output stream.
            """
            return len(text)

        def flush(self) -> typ.NoReturn:
            """Simulate stdout closing after a successful write.

            Raises
            ------
            BrokenPipeError
                Indicates that the output stream closed before flushing.
            """
            msg = "closed stdout during flush"
            raise BrokenPipeError(msg)

    monkeypatch.setattr(sys, "stdout", _FlushFailure())
    counters_before = _observability.counter_snapshot()

    with (
        caplog.at_level("INFO", logger="cmd_mox.shim"),
        _observability.capture_events() as events,
        pytest.raises(SystemExit) as exc,
    ):
        _write_response(Response(stdout="out", exit_code=3), correlation_id="abc")

    _assert_exit_code(exc, 3)
    diagnostic = capsys.readouterr().err
    assert "IPC error: response stream flush failed" in diagnostic, (
        "flush failures must produce a bounded IPC diagnostic"
    )
    assert "closed stdout during flush" not in diagnostic, (
        "Flush exception details must not reach stderr"
    )
    assert "closed stdout during flush" not in caplog.text, (
        "Flush exception details must not reach logs"
    )
    [event] = events
    assert event.operation == "ipc.shim.response_stream", "Wrong event operation"
    assert event.outcome == "flush_failed", "Flush failure outcome was not recorded"
    assert event.error_category == "BrokenPipeError", "Wrong flush error category"
    assert event.correlation_id == "abc", "Flush failure lost its correlation id"
    assert event.duration_ms is not None, "Flush failure lost its duration"
    assert event.duration_ms >= 0, "Flush failure duration is negative"
    counts_after = _observability.counter_snapshot()
    assert (
        counts_after.get(event.key(), 0) == counters_before.get(event.key(), 0) + 1
    ), "Response flush failure did not increment its bounded event counter"


@pytest.mark.parametrize("stream_name", ["stdout", "stderr"])
def test_write_response_preserves_exit_code_after_flush_failure(
    stream_name: str,
) -> None:
    """A child process keeps the chosen status through interpreter shutdown."""
    script = """
import sys
from cmd_mox.ipc import Response
from cmd_mox.shim import _write_response

class FailedStream:
    def write(self, text):
        return len(text)
    def flush(self):
        raise BrokenPipeError("closed stream")

stream_name = "__STREAM_NAME__"
failed_stream = FailedStream()
setattr(sys, stream_name, failed_stream)
setattr(sys, f"__{stream_name}__", failed_stream)
_write_response(Response(exit_code=7, **{stream_name: "payload"}))
""".replace("__STREAM_NAME__", stream_name)
    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - launches a fixed local Python test script
        [sys.executable, "-c", script],
        capture_output=True,
        check=False,
        shell=False,
        text=True,
    )

    assert result.returncode == 7, (
        "interpreter shutdown must preserve the configured response status"
    )
    if stream_name == "stdout":
        assert "IPC error: response stream flush failed" in result.stderr, (
            "stdout failure must report the closed stream to stderr"
        )
