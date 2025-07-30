"""Unit tests for :mod:`cmd_mox.command_runner`."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.command_runner import CommandRunner
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


@pytest.fixture
def runner() -> cabc.Iterator[CommandRunner]:
    """Return a :class:`CommandRunner` with a managed environment."""
    env_mgr = EnvironmentManager()
    env_mgr.__enter__()
    runner = CommandRunner(env_mgr)
    yield runner
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
