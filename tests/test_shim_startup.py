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


class _InteractiveInput:
    """Stub stdin that behaves like an interactive terminal."""

    def __init__(self) -> None:
        self.read_called = False

    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True

    def read(self) -> str:  # pragma: no cover - defensive guard
        self.read_called = True
        msg = "stdin.read() should not be called for ttys"
        raise AssertionError(msg)


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


def test_main_skips_interactive_stdin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``shim.main`` does not read stdin when connected to a tty."""
    captured: dict[str, Invocation] = {}

    def fake_invoke(invocation: Invocation, timeout: float) -> Response:
        captured["invocation"] = invocation
        return Response(stdout="", stderr="", exit_code=0)

    socket_path = tmp_path / "dummy.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    monkeypatch.delenv(CMOX_IPC_TIMEOUT_ENV, raising=False)
    shim_path = tmp_path / "shims" / "alpha"
    monkeypatch.setattr(sys, "argv", [str(shim_path)])
    interactive = _InteractiveInput()
    monkeypatch.setattr(sys, "stdin", interactive)
    monkeypatch.setattr("cmd_mox.shim.invoke_server", fake_invoke)

    import cmd_mox.shim as shim

    with pytest.raises(SystemExit) as excinfo:
        shim.main()

    assert t.cast("SystemExit", excinfo.value).code == 0
    invocation = captured["invocation"]
    assert invocation.stdin == ""
    assert not interactive.read_called


def test_main_honours_custom_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``shim.main`` applies non-default IPC timeout overrides."""
    captured: dict[str, float] = {}

    def fake_invoke(invocation: Invocation, timeout: float) -> Response:
        captured["timeout"] = timeout
        return Response(stdout="custom", stderr="", exit_code=0)

    socket_path = tmp_path / "dummy.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    monkeypatch.setenv(CMOX_IPC_TIMEOUT_ENV, "1.75")
    monkeypatch.setattr(sys, "argv", ["shimcmd"])
    monkeypatch.setattr(sys, "stdin", _FakeInput("ignored"))
    monkeypatch.setattr("cmd_mox.shim.invoke_server", fake_invoke)

    import cmd_mox.shim as shim

    with pytest.raises(SystemExit) as excinfo:
        shim.main()

    assert t.cast("SystemExit", excinfo.value).code == 0
    assert captured["timeout"] == pytest.approx(1.75)
    out = capsys.readouterr()
    assert out.out == "custom"
    assert out.err == ""
