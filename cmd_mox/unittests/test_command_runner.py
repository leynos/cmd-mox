"""Unit tests for :mod:`cmd_mox.command_runner`."""

from __future__ import annotations

import os
import subprocess
import typing as t
from dataclasses import dataclass  # noqa: ICN003
from pathlib import Path

import pytest

from cmd_mox.command_runner import CommandRunner, execute_command
from cmd_mox.environment import EnvironmentManager
from cmd_mox.ipc import Invocation

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


def test_invocation_env_overrides_expectation_env(
    runner: CommandRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Invocation environment should take precedence over expectation env."""
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
    assert captured["VAR"] == "inv"


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
                command="rel",
                which_result="./rel",
                create_file=False,
                exit_code=126,
                stderr="rel: invalid executable path",
            ),
            id="relative",
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


@pytest.mark.parametrize(
    ("exception_factory", "command", "expected_exit_code", "expected_stderr"),
    [
        pytest.param(
            lambda: subprocess.TimeoutExpired(cmd=["sleepy"], timeout=30),
            "sleepy",
            124,
            "sleepy: timeout after 30 seconds",
            id="timeout",
        ),
        pytest.param(
            lambda: FileNotFoundError(),
            "missing",
            127,
            "missing: not found",
            id="file_not_found",
        ),
        pytest.param(
            lambda: PermissionError("denied"),
            "restricted",
            126,
            "restricted: denied",
            id="permission_error",
        ),
        pytest.param(
            lambda: OSError("oops"),
            "broken",
            126,
            "broken: execution failed: oops",
            id="os_error",
        ),
        pytest.param(
            lambda: RuntimeError("boom"),
            "weird",
            126,
            "weird: unexpected error: boom",
            id="unexpected_exception",
        ),
    ],
)
def test_execute_command_handles_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    exception_factory: t.Callable[[], Exception],
    command: str,
    expected_exit_code: int,
    expected_stderr: str,
) -> None:
    """execute_command should translate exceptions into appropriate Responses."""
    invocation = Invocation(command=command, args=[], stdin="", env={})

    def fake_run(*args: object, **kwargs: object) -> DummyResult:
        raise exception_factory()

    monkeypatch.setattr("cmd_mox.command_runner.subprocess.run", fake_run)

    response = execute_command(Path("/bin/true"), invocation, env={}, timeout=30)
    assert response.exit_code == expected_exit_code
    assert response.stderr == expected_stderr
