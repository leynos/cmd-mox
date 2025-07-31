"""Unit tests for :mod:`cmd_mox.command_runner`."""

from __future__ import annotations

import os
import typing as t

import pytest

from cmd_mox.command_runner import CommandRunner
from cmd_mox.environment import EnvironmentManager
from cmd_mox.ipc import Invocation

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc
    from pathlib import Path


class DummyResult:
    """Simplified ``CompletedProcess`` replacement for assertions."""

    def __init__(self, env: dict[str, str]) -> None:
        self.stdout = ""
        self.stderr = ""
        self.returncode = 0
        self.env = env


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
