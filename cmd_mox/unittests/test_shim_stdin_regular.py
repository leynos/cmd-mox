"""Tests for the shim's bounded regular-file stdin strategy."""

from __future__ import annotations

import os
import sys
import threading
import typing as typ

import pytest

from cmd_mox import _stdin_capture as capture
from cmd_mox.shim import _create_invocation
from cmd_mox.unittests._shim_test_support import (
    _assert_exit_code,
    _DummyStdin,
    _InjectedOSError,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


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
