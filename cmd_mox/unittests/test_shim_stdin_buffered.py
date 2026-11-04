"""Tests for buffered stdin read-ahead and descriptor invariants."""

from __future__ import annotations

import os
import sys

import pytest

from cmd_mox import shim
from cmd_mox.shim import _create_invocation
from cmd_mox.unittests._shim_test_support import (
    _assert_exit_code,
    _BufferedInput,
    _DescriptorStdin,
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
    stdin = _DescriptorStdin(stdin_pipe_descriptor_at_eof, buffer=buffer)
    monkeypatch.setattr(sys, "stdin", stdin)
    monkeypatch.setattr(sys, "argv", ["shim"])
    monkeypatch.setattr(shim.time, "monotonic", lambda: clock["value"])

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
