"""Unit tests for shim command and environment setup."""

from __future__ import annotations

import os
import sys
import typing as typ

import pytest

from cmd_mox import shim
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, CMOX_IPC_TIMEOUT_ENV
from cmd_mox.shim import CMOX_SHIM_COMMAND_ENV, _validate_environment
from cmd_mox.unittests.test_shim_support import _assert_exit_code

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_resolve_command_name_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variable should override argv-derived command name."""
    monkeypatch.setenv(CMOX_SHIM_COMMAND_ENV, "shim-alias")
    assert shim._resolve_command_name() == "shim-alias"


def test_resolve_command_name_defaults_to_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback to ``sys.argv`` when no override is provided."""
    monkeypatch.delenv(CMOX_SHIM_COMMAND_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/cmd-mock"])
    assert shim._resolve_command_name() == "cmd-mock"


def test_validate_environment_returns_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Valid environment variables should produce a timeout value."""
    sock_path = tmp_path / "cmd-mox.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, os.fspath(sock_path))
    monkeypatch.delenv(CMOX_IPC_TIMEOUT_ENV, raising=False)

    timeout = _validate_environment()

    assert timeout == pytest.approx(5.0)
    assert os.environ[CMOX_IPC_SOCKET_ENV] == os.fspath(sock_path)


def test_validate_environment_requires_socket(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Socket validation should exit when the environment variable is missing."""
    monkeypatch.delenv(CMOX_IPC_SOCKET_ENV, raising=False)
    monkeypatch.delenv(CMOX_IPC_TIMEOUT_ENV, raising=False)

    with pytest.raises(SystemExit) as exc:
        _validate_environment()

    _assert_exit_code(exc, 1)
    assert "IPC socket not specified" in capsys.readouterr().err


def test_validate_environment_rejects_invalid_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    """Timeout parsing errors should surface as a fatal IPC message."""
    sock_path = tmp_path / "cmd-mox.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, os.fspath(sock_path))
    monkeypatch.setenv(CMOX_IPC_TIMEOUT_ENV, "nan")

    with pytest.raises(SystemExit) as exc:
        _validate_environment()

    _assert_exit_code(exc, 1)
    assert "invalid timeout" in capsys.readouterr().err
