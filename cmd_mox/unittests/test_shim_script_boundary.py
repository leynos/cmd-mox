"""Tests for shim stdin expiry, exit-status forwarding, and script exit."""

from __future__ import annotations

import sys
import threading
import typing as typ

import pytest

from cmd_mox import _stdin_capture as capture

if typ.TYPE_CHECKING:
    import collections.abc as cabc

from cmd_mox import shim
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import Invocation, Response
from cmd_mox.unittests._shim_test_support import _assert_exit_code


class _StalledStdin:
    """Non-interactive stdin whose read blocks until the test releases it."""

    encoding = "utf-8"
    errors = "strict"

    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()

    def isatty(self) -> bool:
        """Report piped stdin so the shim captures it.

        Returns
        -------
        bool
            ``False``, because the double represents a pipe.
        """
        return False

    def read(self) -> str:
        """Block until released, then report the text that arrived too late.

        Returns
        -------
        str
            The text that arrives after the shim abandoned the read.
        """
        self.started.set()
        try:
            self.release.wait(5.0)
            return "late input"
        finally:
            self.finished.set()


@pytest.fixture
def stalled_stdin(monkeypatch: pytest.MonkeyPatch) -> cabc.Iterator[_StalledStdin]:
    """Install a blocking stdin double and always release its worker.

    The double's ``finished`` event fires inside ``read`` itself, so it only
    proves the stream stopped waiting, not that the reader thread exited. The
    process-global worker registry is joined as well, because a leftover live
    worker would make a later ``_run_as_script`` test take the ``os._exit``
    path and fail on its patched exit.

    Yields
    ------
    _StalledStdin
        The installed double, released during fixture teardown.
    """
    stdin = _StalledStdin()
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, "test-socket")
    try:
        yield stdin
    finally:
        stdin.release.set()
        stdin.finished.wait(5.0)
        for worker in capture.outstanding_workers():
            worker.join(5.0)


def test_main_reports_stdin_timeout_without_contacting_ipc(
    stalled_stdin: _StalledStdin,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stalled stdin read exits before any IPC request is dispatched."""
    monkeypatch.setattr(shim, "bootstrap_shim_path", lambda: None)
    monkeypatch.setattr(shim, "_resolve_command_name", lambda: "shim")
    monkeypatch.setattr(shim, "_validate_environment", lambda: 0.05)

    def unexpected_dispatch(*_args: object, **_kwargs: object) -> typ.NoReturn:
        """Reject IPC dispatch after a timed-out stdin capture."""
        pytest.fail("stdin expiry must not dispatch an invocation")

    monkeypatch.setattr(shim, "_execute_invocation", unexpected_dispatch)

    with pytest.raises(SystemExit) as exc:
        shim.main()

    _assert_exit_code(exc, 1)
    assert stalled_stdin.started.is_set(), "the stdin read never started"
    assert capsys.readouterr().err == (
        "IPC error: stdin read timed out after 0.05 seconds\n"
    ), "stdin expiry must report the bounded stdin diagnostic"


@pytest.mark.parametrize("exit_code", [0, 3, 124])
def test_main_forwards_response_exit_codes_unchanged(
    exit_code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Response exit codes, including shim-reserved values, pass through."""
    invocation = Invocation(
        command="shim", args=[], stdin="", env={}, invocation_id="exit-id"
    )
    monkeypatch.setattr(shim, "bootstrap_shim_path", lambda: None)
    monkeypatch.setattr(shim, "_resolve_command_name", lambda: "shim")
    monkeypatch.setattr(shim, "_validate_environment", lambda: 1.0)
    monkeypatch.setattr(sys, "stdin", __import__("io").StringIO(""))
    monkeypatch.setattr(shim, "_create_invocation", lambda *_args, **_kw: invocation)
    monkeypatch.setattr(
        shim,
        "_execute_invocation",
        lambda *_args, **_kw: Response(stdout="", stderr="", exit_code=exit_code),
    )

    with pytest.raises(SystemExit) as exc:
        shim.main()

    _assert_exit_code(exc, exit_code)


def test_main_reports_stdin_read_failure_without_contacting_ipc(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stdin I/O failure exits with the bounded diagnostic, not a traceback."""

    class _FailingStdin:
        def isatty(self) -> bool:
            """Report piped stdin so the shim attempts a read.

            Returns
            -------
            bool
                ``False``, because the double represents a pipe.
            """
            return False

        def read(self) -> str:
            """Fail the capture with a message that must not leak.

            Raises
            ------
            OSError
                Always, carrying a sentinel message.
            """
            msg = "secret detail"
            raise OSError(msg)

    monkeypatch.setattr(shim, "bootstrap_shim_path", lambda: None)
    monkeypatch.setattr(shim, "_resolve_command_name", lambda: "shim")
    monkeypatch.setattr(shim, "_validate_environment", lambda: 1.0)
    monkeypatch.setattr(sys, "stdin", _FailingStdin())
    monkeypatch.setattr(
        shim, "_execute_invocation", lambda *_a, **_k: pytest.fail("dispatched")
    )

    with pytest.raises(SystemExit) as exc:
        shim.main()

    _assert_exit_code(exc, 1)
    diagnostic = capsys.readouterr().err
    assert diagnostic == "IPC error: stdin read failed\n", (
        "a stdin read failure must use the bounded diagnostic"
    )
    assert "secret detail" not in diagnostic, "stdin error text leaked to stderr"


def test_main_reports_a_failing_isatty_without_contacting_ipc(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A stream that cannot report its type still gets the bounded diagnostic.

    ``isatty()`` is the shim's first stdin call, and a closed text stream raises
    ``ValueError`` from it. Probing before the bounded handler would therefore
    surface that failure as an unhandled traceback instead of the diagnostic
    every other stdin failure receives.
    """

    class _UnprobeableStdin:
        def isatty(self) -> bool:
            """Fail the way a closed text stream does.

            Raises
            ------
            ValueError
                Always, because the stream is closed.
            """
            msg = "I/O operation on closed file"
            raise ValueError(msg)

    monkeypatch.setattr(shim, "bootstrap_shim_path", lambda: None)
    monkeypatch.setattr(shim, "_resolve_command_name", lambda: "shim")
    monkeypatch.setattr(shim, "_validate_environment", lambda: 1.0)
    monkeypatch.setattr(sys, "stdin", _UnprobeableStdin())
    monkeypatch.setattr(
        shim, "_execute_invocation", lambda *_a, **_k: pytest.fail("dispatched")
    )

    with pytest.raises(SystemExit) as exc:
        shim.main()

    _assert_exit_code(exc, 1)
    assert capsys.readouterr().err == "IPC error: stdin read failed\n", (
        "an unprobeable stdin must use the bounded diagnostic"
    )


def test_run_as_script_exits_without_joining_an_abandoned_worker(
    stalled_stdin: _StalledStdin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The script boundary leaves the process without waiting for stdin."""
    exited: list[int] = []

    def fake_main() -> typ.NoReturn:
        """Capture stdin until the deadline expires, then exit.

        Raises
        ------
        SystemExit
            Always, with status ``1``.
        """
        with pytest.raises(SystemExit):
            shim._create_invocation("shim", timeout=0.05)
        raise SystemExit(1)

    monkeypatch.setattr(shim, "main", fake_main)
    monkeypatch.setattr(shim.os, "_exit", exited.append)

    shim._run_as_script()

    assert exited == [1], "the script boundary did not exit with the shim status"
    assert capture.outstanding_workers(), (
        "the blocked reader was not registered for the script boundary"
    )


def test_run_as_script_reraises_when_no_worker_was_abandoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A normal shim exit keeps the usual interpreter shutdown."""

    def fake_main() -> typ.NoReturn:
        """Exit as a successful shim invocation would.

        Raises
        ------
        SystemExit
            Always, with status ``0``.
        """
        raise SystemExit(0)

    monkeypatch.setattr(shim, "main", fake_main)
    # The registry is process-global, and other modules deliberately abandon
    # workers of their own. Pin it empty so this test asserts the normal exit
    # path rather than whatever an earlier test happened to leave behind.
    monkeypatch.setattr(capture, "outstanding_workers", lambda: ())
    monkeypatch.setattr(
        shim.os, "_exit", lambda _status: pytest.fail("normal exits must not _exit")
    )

    with pytest.raises(SystemExit) as exc:
        shim._run_as_script()

    _assert_exit_code(exc, 0)


def test_run_as_script_maps_a_message_status_to_failure(
    stalled_stdin: _StalledStdin,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-integer exit status is reported as a failure, not as success."""
    exited: list[int] = []

    def fake_main() -> typ.NoReturn:
        """Abandon a stdin worker, then exit with a message status.

        Raises
        ------
        SystemExit
            Always, carrying a message rather than an integer status.
        """
        with pytest.raises(SystemExit):
            shim._create_invocation("shim", timeout=0.05)
        raise SystemExit("fatal")

    monkeypatch.setattr(shim, "main", fake_main)
    monkeypatch.setattr(shim.os, "_exit", exited.append)

    shim._run_as_script()

    assert exited == [1], "a message status must never be reported as success"
    assert stalled_stdin.started.is_set(), "the stdin read never started"


def test_exit_without_joining_workers_tolerates_closed_streams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A closed response stream must not change the reported status."""
    exited: list[int] = []

    class _ClosedStream:
        def flush(self) -> typ.NoReturn:
            """Behave like a stream closed by the caller.

            Raises
            ------
            ValueError
                Always, as a closed text stream does.
            """
            raise ValueError

    monkeypatch.setattr(sys, "stdout", _ClosedStream())
    monkeypatch.setattr(sys, "stderr", _ClosedStream())
    monkeypatch.setattr(shim.os, "_exit", exited.append)

    shim._exit_without_joining_workers(7)

    assert exited == [7], "a closed stream changed the reported status"
