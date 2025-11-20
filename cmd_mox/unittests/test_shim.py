"""Unit tests for shim helper utilities."""

from __future__ import annotations

import os
import sys
import typing as t
from pathlib import Path

import pytest

import cmd_mox.shim as shim
from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    CMOX_REAL_COMMAND_ENV_PREFIX,
)
from cmd_mox.ipc import Invocation, PassthroughRequest, Response
from cmd_mox.shim import (
    CMOX_SHIM_COMMAND_ENV,
    _create_invocation,
    _execute_invocation,
    _merge_passthrough_path,
    _resolve_passthrough_target,
    _validate_environment,
    _validate_override_path,
    _write_response,
)


def test_resolve_command_name_prefers_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variable should override argv-derived command name."""
    monkeypatch.setenv(CMOX_SHIM_COMMAND_ENV, "shim-alias")
    assert shim._resolve_command_name() == "shim-alias"


def test_resolve_command_name_defaults_to_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fallback to ``sys.argv`` when no override is provided."""
    monkeypatch.delenv(CMOX_SHIM_COMMAND_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/cmd-mock"])
    assert shim._resolve_command_name() == "cmd-mock"


class _DummyStdin:
    """Test double to simulate ``sys.stdin`` behaviour."""

    def __init__(self, data: str, *, is_tty: bool) -> None:
        self._data = data
        self._is_tty = is_tty
        self.read_calls = 0

    def isatty(self) -> bool:
        return self._is_tty

    def read(self) -> str:
        self.read_calls += 1
        return self._data


def _assert_exit_code(exc: pytest.ExceptionInfo[BaseException], expected: int) -> None:
    """Assert that *exc* wraps a :class:`SystemExit` with the desired code."""
    err = exc.value
    assert isinstance(err, SystemExit)
    assert err.code == expected


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


def test_create_invocation_skips_tty_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """When stdin is a TTY, invocation payload should contain empty stdin."""
    dummy_stdin = _DummyStdin("ignored", is_tty=True)
    monkeypatch.setattr(sys, "stdin", dummy_stdin)
    monkeypatch.setattr(sys, "argv", ["shim", "--flag"])
    monkeypatch.setenv("EXTRA", "value")

    invocation = _create_invocation("shim")

    assert invocation.command == "shim"
    assert invocation.args == ["--flag"]
    assert invocation.stdin == ""
    assert dummy_stdin.read_calls == 0
    assert invocation.env["EXTRA"] == "value"


def test_create_invocation_reads_stdin_when_not_tty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-TTY stdin should be captured into the invocation."""
    dummy_stdin = _DummyStdin("payload", is_tty=False)
    monkeypatch.setattr(sys, "stdin", dummy_stdin)
    monkeypatch.setattr(sys, "argv", ["shim"])

    invocation = _create_invocation("shim")

    assert invocation.stdin == "payload"
    assert dummy_stdin.read_calls == 1


def test_execute_invocation_returns_response_without_passthrough(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regular IPC responses should be returned directly."""
    invocation = Invocation(
        command="cmd", args=[], stdin="", env={}, invocation_id="abc"
    )
    expected = Response(stdout="ok", stderr="", exit_code=0)

    calls: dict[str, t.Any] = {}

    def fake_invoke(inv: Invocation, timeout: float) -> Response:
        calls["invocation"] = inv
        calls["timeout"] = timeout
        return expected

    monkeypatch.setattr(shim, "invoke_server", fake_invoke)
    monkeypatch.setattr(
        shim,
        "_handle_passthrough",
        lambda *args, **kwargs: pytest.fail("passthrough handler should not run"),
    )

    result = _execute_invocation(invocation, timeout=1.5)

    assert result is expected
    assert calls["invocation"] is invocation
    assert calls["timeout"] == 1.5


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

    def fake_passthrough(inv: Invocation, resp: Response, timeout: float) -> Response:
        assert inv is invocation
        assert resp is intermediate
        assert timeout == 2.0
        return final

    monkeypatch.setattr(shim, "_handle_passthrough", fake_passthrough)

    result = _execute_invocation(invocation, timeout=2.0)

    assert result is final


def test_execute_invocation_surfaces_ipc_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exceptions raised by IPC helpers should trigger a controlled exit."""
    invocation = Invocation(
        command="cmd", args=[], stdin="", env={}, invocation_id="abc"
    )

    def raise_error(*_: object, **__: object) -> t.NoReturn:
        raise OSError("boom")

    monkeypatch.setattr(shim, "invoke_server", raise_error)

    with pytest.raises(SystemExit) as exc:
        _execute_invocation(invocation, timeout=1.0)

    _assert_exit_code(exc, 1)
    assert "IPC error: boom" in capsys.readouterr().err


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


@pytest.mark.parametrize(
    ("factory", "expected_exit", "expected_message"),
    [
        (
            lambda tmp_path: tmp_path / "missing",  # missing file
            127,
            "not found",
        ),
        (
            lambda tmp_path: tmp_path,  # directory
            126,
            "invalid executable path",
        ),
        (
            lambda tmp_path: _make_directory_symlink(tmp_path),
            126,
            "invalid executable path",
        ),
    ],
)
def test_validate_override_path_reports_missing_or_invalid_targets(
    tmp_path: Path,
    factory: t.Callable[[Path], Path],
    expected_exit: int,
    expected_message: str,
) -> None:
    """Validate error handling for nonexistent and non-file overrides."""
    target = factory(tmp_path)
    result = _validate_override_path("tool", os.fspath(target))

    assert isinstance(result, Response)
    assert result.exit_code == expected_exit
    assert expected_message in result.stderr


def _make_directory_symlink(tmp_path: Path) -> Path:
    """Return a symlink to a directory for override validation tests."""
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    symlink = tmp_path / "dir-link"
    if not hasattr(os, "symlink"):
        pytest.skip("Platform does not support symlinks")
    try:
        symlink.symlink_to(target_dir, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - windows without admin rights
        pytest.skip(f"Symlinks unavailable: {exc}")
    return symlink


def test_validate_override_path_rejects_non_executable_file(tmp_path: Path) -> None:
    """Non-executable override files should surface an exit code of 126."""
    script = tmp_path / "tool"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o644)

    result = _validate_override_path("tool", os.fspath(script))

    assert isinstance(result, Response)
    assert result.exit_code == 126
    assert "not executable" in result.stderr


def test_validate_override_path_accepts_relative_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative paths are resolved against the current working directory."""
    script = tmp_path / "tool"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    monkeypatch.chdir(tmp_path)

    result = _validate_override_path("tool", "tool")

    assert isinstance(result, Path)
    assert result == script
    assert result.is_absolute()


def test_merge_passthrough_path_filters_shim_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shim directory should be removed when constructing lookup paths."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    socket_path = shim_dir / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, os.fspath(socket_path))

    env_path = os.pathsep.join([os.fspath(shim_dir), "/usr/bin", "/opt/tools"])
    lookup_path = os.pathsep.join(["/custom/bin", "/usr/bin"])

    merged = _merge_passthrough_path(env_path, lookup_path)

    assert merged.split(os.pathsep) == ["/usr/bin", "/opt/tools", "/custom/bin"]


def test_merge_passthrough_path_is_case_insensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate entries differing only in case should collapse on Windows."""
    monkeypatch.setattr(shim, "IS_WINDOWS", True)
    shim_dir = tmp_path / "Shim"
    shim_dir.mkdir()
    socket_path = shim_dir / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, os.fspath(socket_path))

    separator = ";" if shim.IS_WINDOWS else os.pathsep
    env_path = separator.join([os.fspath(shim_dir), r"C:\Tools"])
    lookup_path = separator.join([r"c:\tools", r"C:\Other"])

    merged = shim._merge_passthrough_path(env_path, lookup_path)

    assert merged.split(separator) == [r"C:\Tools", r"C:\Other"]


def test_resolve_passthrough_target_prefers_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment overrides should bypass PATH resolution entirely."""
    script = tmp_path / "real"
    script.write_text("#!/bin/sh\necho real\n")
    script.chmod(0o755)

    monkeypatch.setenv(f"{CMOX_REAL_COMMAND_ENV_PREFIX}echo", os.fspath(script))

    directive = PassthroughRequest(
        invocation_id="abc",
        lookup_path="/bin",
        extra_env={},
        timeout=30,
    )
    invocation = Invocation(command="echo", args=[], stdin="", env={})
    env = {"PATH": "/usr/bin"}

    resolved = _resolve_passthrough_target(invocation, directive, env)

    assert resolved == script


def test_resolve_passthrough_target_merges_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATH resolution should exclude the shim directory and de-duplicate entries."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    socket_path = shim_dir / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, os.fspath(socket_path))

    invocation = Invocation(command="echo", args=[], stdin="", env={})
    directive = PassthroughRequest(
        invocation_id="abc",
        lookup_path=os.pathsep.join(["/custom/bin", "/usr/bin"]),
        extra_env={},
        timeout=30,
    )
    env = {
        "PATH": _merge_passthrough_path(
            os.pathsep.join([os.fspath(shim_dir), "/usr/bin"]),
            directive.lookup_path,
        )
    }

    captured_path: str | None = None

    def fake_resolve(command: str, path: str, override: str | None = None) -> Path:
        nonlocal captured_path
        captured_path = path
        assert override is None
        return Path("/usr/bin/echo")

    monkeypatch.setattr(shim, "resolve_command_with_override", fake_resolve)

    resolved = _resolve_passthrough_target(invocation, directive, env)

    assert isinstance(resolved, Path)
    assert captured_path == env["PATH"]
