"""Unit tests for shim startup behaviour."""

from __future__ import annotations

import io
import os
import sys
from typing import Any

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, CMOX_IPC_TIMEOUT_ENV
from cmd_mox.ipc import Invocation, Response


class _FakeInput(io.StringIO):
    """StringIO that reports itself as a non-tty stream."""

    def isatty(self) -> bool:  # pragma: no cover - trivial
        return False


def test_main_reports_invocation_details(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """``shim.main`` forwards invocation metadata and applies the response."""

    captured: dict[str, Any] = {}

    def fake_invoke(invocation: Invocation, timeout: float) -> Response:
        captured["invocation"] = invocation
        captured["timeout"] = timeout
        return Response(stdout="out", stderr="err", exit_code=7, env={"EXTRA": "42"})

    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, "/tmp/dummy.sock")
    monkeypatch.delenv(CMOX_IPC_TIMEOUT_ENV, raising=False)
    monkeypatch.setenv("SAMPLE", "value")
    monkeypatch.delenv("EXTRA", raising=False)
    monkeypatch.setattr(sys, "argv", ["/tmp/shims/git", "status", "--short"])
    monkeypatch.setattr(sys, "stdin", _FakeInput("payload"))
    monkeypatch.setattr("cmd_mox.shim.invoke_server", fake_invoke)

    import cmd_mox.shim as shim

    with pytest.raises(SystemExit) as excinfo:
        shim.main()

    assert excinfo.value.code == 7

    out = capsys.readouterr()
    assert out.out == "out"
    assert out.err == "err"
    assert os.environ["EXTRA"] == "42"

    invocation = captured["invocation"]
    assert invocation.command == "git"
    assert invocation.args == ["status", "--short"]
    assert invocation.stdin == "payload"
    assert invocation.env.get("SAMPLE") == "value"
    assert captured["timeout"] == pytest.approx(5.0)
