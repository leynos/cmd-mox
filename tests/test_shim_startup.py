"""Unit tests for shim startup behaviour."""

from __future__ import annotations

import io
import os
import sys
import typing as t

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, CMOX_IPC_TIMEOUT_ENV
from cmd_mox.ipc import Invocation, Response

if t.TYPE_CHECKING:  # pragma: no cover - import used only for typing
    from pathlib import Path


class _FakeInput(io.StringIO):
    """StringIO that reports itself as a non-tty stream."""

    def isatty(self) -> bool:  # pragma: no cover - trivial
        return False


def test_main_reports_invocation_details(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``shim.main`` forwards invocation metadata and applies the response."""
    captured: dict[str, t.Any] = {}

    def fake_invoke(invocation: Invocation, timeout: float) -> Response:
        captured["invocation"] = invocation
        captured["timeout"] = timeout
        return Response(stdout="out", stderr="err", exit_code=7, env={"EXTRA": "42"})

    socket_path = tmp_path / "dummy.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    monkeypatch.delenv(CMOX_IPC_TIMEOUT_ENV, raising=False)
    monkeypatch.setenv("SAMPLE", "value")
    monkeypatch.delenv("EXTRA", raising=False)
    shim_path = tmp_path / "shims" / "git"
    monkeypatch.setattr(sys, "argv", [str(shim_path), "status", "--short"])
    monkeypatch.setattr(sys, "stdin", _FakeInput("payload"))
    monkeypatch.setattr("cmd_mox.shim.invoke_server", fake_invoke)

    import cmd_mox.shim as shim

    with pytest.raises(SystemExit) as excinfo:
        shim.main()

    assert isinstance(excinfo.value, SystemExit)
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
