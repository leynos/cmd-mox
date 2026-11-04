"""Tests for regular-file stdin reads and their deadline bound."""

from __future__ import annotations

import os
import sys
import tempfile
import threading

import pytest

from cmd_mox import shim
from cmd_mox.shim import _create_invocation
from cmd_mox.unittests._shim_test_support import _assert_exit_code, _DummyStdin


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
