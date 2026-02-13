"""Unit tests for shim timeout validation."""

import sys
from pathlib import Path

import pytest

import cmd_mox.shim as shim
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, CMOX_IPC_TIMEOUT_ENV

pytestmark = pytest.mark.requires_unix_sockets


@pytest.mark.parametrize(
    "value",
    ["-1", "0", "nan", "inf", "abc", "", " "],
)
def test_main_errors_on_invalid_timeout(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """``shim.main`` exits with code 1 for invalid timeouts."""
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(tmp_path / "sock"))
    monkeypatch.setenv(CMOX_IPC_TIMEOUT_ENV, value)

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["shim"])

    with pytest.raises(SystemExit) as exc:
        shim.main()

    assert exc.value.code == 1
    stderr = capsys.readouterr().err
    assert f"invalid timeout: '{value}'" in stderr
