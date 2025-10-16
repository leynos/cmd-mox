"""Unit tests for :mod:`cmd_mox.command_runner`."""

from __future__ import annotations

import os
import subprocess
import typing as t
from dataclasses import dataclass  # noqa: ICN003
from pathlib import Path

import pytest

from cmd_mox.command_runner import (
    CommandRunner,
    execute_command,
    resolve_command_path,
    resolve_command_with_override,
)
from cmd_mox.environment import CMOX_REAL_COMMAND_ENV_PREFIX, EnvironmentManager
from cmd_mox.ipc import Invocation, Response

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc


class DummyResult:
    """Simplified ``CompletedProcess`` replacement for assertions."""

    def __init__(self, env: dict[str, str]) -> None:
        self.stdout = ""
        self.stderr = ""
        self.returncode = 0
        self.env = env


@dataclass
class CommandTestScenario:
    """Test case data for invalid command scenarios."""

    command: str
    which_result: str | None
    create_file: bool
    exit_code: int
    stderr: str

    def get_which_result_for_file_creation(self) -> str:
        """Return ``which_result`` when a file should be created."""
        assert self.create_file
        assert self.which_result is not None
        return self.which_result


@pytest.fixture
def runner() -> cabc.Iterator[CommandRunner]:
    """Return a :class:`CommandRunner` with a managed environment."""
    env_mgr = EnvironmentManager()
    env_mgr.__enter__()
    yield CommandRunner(env_mgr)
    env_mgr.__exit__(None, None, None)


def test_extra_env_overrides_invocation_env(
    runner: CommandRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extra environment variables should override invocation values."""
    captured: dict[str, str] = {}

    def fake_run(
        argv: list[str], *, env: dict[str, str], **_kwargs: object
    ) -> DummyResult:
        nonlocal captured
        captured = env
        return DummyResult(env)

    monkeypatch.setattr("cmd_mox.command_runner.subprocess.run", fake_run)

    invocation = Invocation(command="echo", args=[], stdin="", env={"VAR": "inv"})
    runner.run(invocation, {"VAR": "expect"})
    assert captured["VAR"] == "expect"


def test_fallback_to_system_path(
    runner: CommandRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fallback to ``os.environ['PATH']`` when original PATH is missing."""
    dummy = tmp_path / "dummy"
    dummy.write_text("echo hi")
    dummy.chmod(0o755)

    monkeypatch.setenv("PATH", str(tmp_path))
    old_path = runner._env_mgr.original_environment.pop("PATH", None)

    captured_path: str | None = None

    def fake_which(cmd: str, path: str | None = None) -> str | None:
        nonlocal captured_path
        captured_path = path
        return str(dummy) if cmd == "dummy" else None

    def fake_run(
        argv: list[str], *, env: dict[str, str], **_kwargs: object
    ) -> DummyResult:
        return DummyResult(env)

    monkeypatch.setattr("cmd_mox.command_runner.shutil.which", fake_which)
    monkeypatch.setattr("cmd_mox.command_runner.subprocess.run", fake_run)

    invocation = Invocation(command="dummy", args=[], stdin="", env={})
    runner.run(invocation, {})

    if old_path is not None:
        runner._env_mgr.original_environment["PATH"] = old_path

    assert captured_path == os.environ["PATH"]


# Error conditions for resolving commands via shutil.which
@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            CommandTestScenario(
                command="missing",
                which_result=None,
                create_file=False,
                exit_code=127,
                stderr="missing: not found",
            ),
            id="missing",
        ),
        pytest.param(
            CommandTestScenario(
                command="dummy",
                which_result="dummy",
                create_file=True,
                exit_code=126,
                stderr="dummy: not executable",
            ),
            id="non-executable",
        ),
    ],
)
def test_run_error_conditions(
    runner: CommandRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: CommandTestScenario,
) -> None:
    """Return consistent errors for invalid or missing commands."""
    if scenario.create_file:
        dummy = tmp_path / scenario.get_which_result_for_file_creation()
        dummy.write_text("echo hi")
        dummy.chmod(0o644)
        result_path = str(dummy)
    else:
        result_path = scenario.which_result

    monkeypatch.setattr(
        "cmd_mox.command_runner.shutil.which", lambda cmd, path=None: result_path
    )

    invocation = Invocation(command=scenario.command, args=[], stdin="", env={})
    response = runner.run(invocation, {})

    assert response.exit_code == scenario.exit_code
    assert response.stderr == scenario.stderr


def test_resolve_command_path_accepts_relative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Relative results from ``shutil.which`` should be resolved successfully."""
    script = tmp_path / "rel"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "cmd_mox.command_runner.shutil.which",
        lambda command, path=None: "./rel" if command == "rel" else None,
    )

    resolved = resolve_command_path("rel", str(tmp_path))
    assert resolved == script


def test_resolve_command_path_accepts_absolute(tmp_path: Path) -> None:
    """Absolute command paths should be validated directly."""
    script = tmp_path / "absolute"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    resolved = resolve_command_path(str(script), "/usr/bin")
    assert resolved == script.resolve()


def test_resolve_command_path_rejects_missing_absolute(tmp_path: Path) -> None:
    """Missing absolute paths should surface a 127 exit code."""
    missing = tmp_path / "missing"

    result = resolve_command_path(str(missing), "/usr/bin")
    assert isinstance(result, Response)
    assert result.exit_code == 127


def test_resolve_command_path_rejects_invalid_relative(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Relative paths must still resolve to executable files."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "cmd_mox.command_runner.shutil.which",
        lambda command, path=None: "./missing" if command == "missing" else None,
    )

    result = resolve_command_path("missing", str(tmp_path))
    assert isinstance(result, Response)
    assert result.exit_code == 127


def test_resolve_command_with_override_uses_override(tmp_path: Path) -> None:
    """Overrides should bypass PATH lookups entirely."""
    script = tmp_path / "real"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)

    resolved = resolve_command_with_override("tool", "/usr/bin", str(script))
    assert resolved == script.resolve()


def test_resolve_command_with_override_reports_errors(tmp_path: Path) -> None:
    """Invalid overrides should return error responses."""
    missing = tmp_path / "nope"

    result = resolve_command_with_override("tool", "/usr/bin", str(missing))
    assert isinstance(result, Response)
    assert result.exit_code == 127


@dataclass(frozen=True)
class ExecuteExceptionScenario:
    """Bundle execute_command exception expectations for parametrized tests."""

    factory: t.Callable[[], Exception]
    command: str
    exit_code: int
    stderr: str


@pytest.mark.parametrize(
    "scenario",
    [
        pytest.param(
            ExecuteExceptionScenario(
                factory=lambda: subprocess.TimeoutExpired(cmd=["sleepy"], timeout=30),
                command="sleepy",
                exit_code=124,
                stderr="sleepy: timeout after 30 seconds",
            ),
            id="timeout",
        ),
        pytest.param(
            ExecuteExceptionScenario(
                factory=lambda: FileNotFoundError(),
                command="missing",
                exit_code=127,
                stderr="missing: not found",
            ),
            id="file_not_found",
        ),
        pytest.param(
            ExecuteExceptionScenario(
                factory=lambda: PermissionError("denied"),
                command="restricted",
                exit_code=126,
                stderr="restricted: denied",
            ),
            id="permission_error",
        ),
        pytest.param(
            ExecuteExceptionScenario(
                factory=lambda: OSError("oops"),
                command="broken",
                exit_code=126,
                stderr="broken: execution failed: oops",
            ),
            id="os_error",
        ),
        pytest.param(
            ExecuteExceptionScenario(
                factory=lambda: RuntimeError("boom"),
                command="weird",
                exit_code=126,
                stderr="weird: unexpected error: boom",
            ),
            id="unexpected_exception",
        ),
    ],
)
def test_execute_command_handles_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    scenario: ExecuteExceptionScenario,
) -> None:
    """execute_command should translate exceptions into appropriate Responses."""
    invocation = Invocation(command=scenario.command, args=[], stdin="", env={})

    def fake_run(*args: object, **kwargs: object) -> DummyResult:
        raise scenario.factory()

    monkeypatch.setattr("cmd_mox.command_runner.subprocess.run", fake_run)

    response = execute_command(Path("/bin/true"), invocation, env={}, timeout=30)
    assert response.exit_code == scenario.exit_code
    assert response.stderr == scenario.stderr


def test_command_runner_honours_override_environment(
    runner: CommandRunner,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Overrides from ``CMOX_REAL_COMMAND_*`` should dictate execution path."""
    script = tmp_path / "echo"
    script.write_text("#!/bin/sh\nexit 0\n")
    script.chmod(0o755)
    monkeypatch.setenv(f"{CMOX_REAL_COMMAND_ENV_PREFIX}echo", str(script))

    captured: list[str] = []

    def fake_run(
        argv: list[str], *, env: dict[str, str], **kwargs: object
    ) -> DummyResult:
        captured.extend(argv)
        return DummyResult(env)

    monkeypatch.setattr("cmd_mox.command_runner.subprocess.run", fake_run)

    invocation = Invocation(command="echo", args=["hello"], stdin="", env={})
    runner.run(invocation, {})

    assert captured[0] == str(script)
