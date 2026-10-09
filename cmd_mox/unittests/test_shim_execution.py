"""Tests for shim invocation, passthrough, and response handling."""

from __future__ import annotations

import math
import os
import subprocess
import sys
import typing as typ

import pytest

from cmd_mox import shim
from cmd_mox.ipc import Invocation, PassthroughRequest, PassthroughResult, Response
from cmd_mox.shim import _execute_invocation, _write_response
from cmd_mox.unittests.test_shim_support import (
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
        calls["invocation"] = inv
        calls["timeout"] = timeout
        calls["deadline"] = deadline
        return expected

    monkeypatch.setattr(shim, "invoke_server", fake_invoke)

    def fail_passthrough(*_args: object, **_kwargs: object) -> typ.NoReturn:
        return pytest.fail("passthrough handler should not run")

    monkeypatch.setattr(
        shim,
        "_handle_passthrough",
        fail_passthrough,  # ty misreads @_with_exception
    )

    result = _execute_invocation(invocation, timeout=1.5)

    assert result is expected
    assert calls["invocation"] is invocation
    assert math.isclose(calls["timeout"], 1.5)
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
        assert inv is invocation
        assert resp is intermediate
        assert math.isclose(timeout, 2.0)
        assert deadline is None
        return final

    monkeypatch.setattr(shim, "_handle_passthrough", fake_passthrough)

    result = _execute_invocation(invocation, timeout=2.0)

    assert result is final


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
        timeouts.append(timeout)
        deadlines.append(deadline)
        return final

    monkeypatch.setattr(shim, "report_passthrough_result", fake_report)

    result = _execute_invocation(invocation, timeout=1.0, deadline=101.0)

    assert result is final
    assert timeouts == pytest.approx([1.0, 0.4])
    assert deadlines == [101.0, 101.0], "The absolute deadline must span both IPC calls"


def test_timeout_remaining_without_deadline_returns_configured_timeout() -> None:
    """No shared deadline preserves the cooperative timeout value."""
    assert shim._timeout_remaining(2.5, None) == pytest.approx(2.5), (
        "A missing deadline must leave the configured timeout unchanged"
    )


def test_timeout_remaining_keeps_shim_timeout_diagnostic() -> None:
    """An expired shared deadline retains the shim's established message."""
    with pytest.raises(TimeoutError, match="IPC operation timed out"):
        shim._timeout_remaining(2.5, 0.0)


def test_execute_invocation_surfaces_ipc_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exceptions raised by IPC helpers should trigger a controlled exit."""
    invocation = Invocation(
        command="cmd", args=[], stdin="", env={}, invocation_id="abc"
    )

    def raise_error(*_: object, **__: object) -> typ.NoReturn:
        raise OSError("boom")

    monkeypatch.setattr(shim, "invoke_server", raise_error)

    with pytest.raises(SystemExit) as exc:
        _execute_invocation(invocation, timeout=1.0)

    _assert_exit_code(exc, 1)
    assert "IPC error: boom" in capsys.readouterr().err


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

    monkeypatch.setattr(shim, "_run_real_command", lambda *_args: Response(exit_code=2))

    def raise_error(*_: object, **__: object) -> typ.NoReturn:
        msg = "server did not acknowledge passthrough"
        raise TimeoutError(msg)

    monkeypatch.setattr(shim, "report_passthrough_result", raise_error)

    with pytest.raises(SystemExit) as exc:
        shim._handle_passthrough(invocation, response, timeout=1.0)

    _assert_exit_code(exc, 2)
    assert (
        "IPC error: server did not acknowledge passthrough" in capsys.readouterr().err
    ), "passthrough report failures must be diagnosed"


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
    assert captured.out == "out"
    assert captured.err == "err"
    assert os.environ["NEW"] == "value"


def test_write_response_handles_closed_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A closed output stream produces a controlled IPC failure."""

    class _ClosedWriter:
        def write(self, _text: str) -> typ.NoReturn:
            msg = "closed stdout"
            raise BrokenPipeError(msg)

    monkeypatch.setattr(sys, "stdout", _ClosedWriter())

    with pytest.raises(SystemExit) as exc:
        _write_response(Response(stdout="out", stderr="server diagnostic", exit_code=3))

    _assert_exit_code(exc, 3)
    stderr = capsys.readouterr().err
    assert "server diagnostic" in stderr, (
        "stdout failure must not discard the response stderr"
    )
    assert "IPC error: closed stdout" in stderr, (
        "closed output streams must produce an IPC diagnostic"
    )


def test_write_response_handles_stdout_flush_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A deferred output failure keeps the response exit code."""

    class _FlushFailure:
        def write(self, text: str) -> int:
            return len(text)

        def flush(self) -> typ.NoReturn:
            msg = "closed stdout during flush"
            raise BrokenPipeError(msg)

    monkeypatch.setattr(sys, "stdout", _FlushFailure())

    with pytest.raises(SystemExit) as exc:
        _write_response(Response(stdout="out", exit_code=3))

    _assert_exit_code(exc, 3)
    assert "IPC error: closed stdout during flush" in capsys.readouterr().err, (
        "flush failures must produce a controlled IPC diagnostic"
    )


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
        assert "IPC error: closed stream" in result.stderr, (
            "stdout failure must report the closed stream to stderr"
        )
