"""pytest-bdd steps for controller replay integration scenarios."""

from __future__ import annotations

import json
import shlex
import typing as typ

import pytest
from pytest_bdd import given, parsers, then, when

from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.ipc import Invocation
from cmd_mox.record.fixture import RecordedInvocation
from tests.helpers.fixtures import write_minimal_replay_fixture, write_replay_fixture
from tests.steps.command_execution import _resolve_command, _run

if typ.TYPE_CHECKING:
    import subprocess
    from pathlib import Path

    from cmd_mox.controller import CmdMox


@given("a git replay fixture exists", target_fixture="git_replay_fixture_path")
def git_replay_fixture(tmp_path: Path) -> Path:
    """Create a replay fixture containing ``git status`` -> ``ok``.

    Returns
    -------
    Path
        The path to the written fixture file.
    """
    return write_minimal_replay_fixture(tmp_path)


@given(parsers.cfparse('the command "{cmd}" is spied with that replay fixture'))
def spy_with_replay_fixture(
    mox: CmdMox, cmd: str, git_replay_fixture_path: Path
) -> None:
    """Attach a strict replay fixture to a spy."""
    mox.spy(cmd).replay(git_replay_fixture_path)


@given(
    parsers.cfparse(
        'the command "{cmd}" is spied with fuzzy replay and fallback "{text}"'
    )
)
def spy_with_fuzzy_replay_and_fallback(
    mox: CmdMox, cmd: str, text: str, git_replay_fixture_path: Path
) -> None:
    """Attach a fuzzy replay fixture and canned fallback response to a spy."""
    mox.spy(cmd).returns(stdout=text).replay(git_replay_fixture_path, strict=False)


@when(
    parsers.cfparse(
        'the controller handles the invocation for "{cmd}" with arguments "{args}" '
        "expecting UnexpectedCommandError"
    ),
    target_fixture="invocation_error",
)
def handle_invocation_expect_error(
    mox: CmdMox, cmd: str, args: str
) -> UnexpectedCommandError:
    """Call ``_handle_invocation()`` directly and capture the strict replay error.

    Returns
    -------
    UnexpectedCommandError
        The exception raised by the unmatched invocation.
    """
    invocation = Invocation(command=cmd, args=shlex.split(args), stdin="", env={})

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox._handle_invocation(invocation)

    return excinfo.value


@given(
    parsers.cfparse(
        'a git replay fixture with recordings "{first}" and "{second}" exists'
    ),
    target_fixture="git_replay_fixture_path",
)
def git_replay_fixture_pair(tmp_path: Path, first: str, second: str) -> Path:
    """Write a fixture holding two ``git status`` recordings, in sequence order.

    Returns
    -------
    Path
        The path to the written fixture file.
    """
    recordings = [
        RecordedInvocation(
            sequence=index,
            command="git",
            args=["status"],
            stdin="",
            env_subset={},
            stdout=f"{stdout}\n",
            stderr="",
            exit_code=0,
            timestamp="2026-01-15T10:30:00+00:00",
            duration_ms=0,
        )
        for index, stdout in enumerate((first, second))
    ]
    return write_replay_fixture(tmp_path, recordings, filename="git_pair.json")


@when(
    parsers.cfparse(
        'I run the command "{cmd}" with arguments "{args}" allowing failure'
    ),
    target_fixture="result",
)
def run_command_allowing_failure(
    mox: CmdMox, cmd: str, args: str
) -> subprocess.CompletedProcess[str]:
    """Run *cmd* with *args*, tolerating a non-zero exit status.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed command process and its captured output.
    """
    argv = [_resolve_command(cmd), *shlex.split(args)]
    return _run(argv, check=False)


@given("a corrupt git replay fixture exists", target_fixture="corrupt_git_fixture_path")
def corrupt_git_replay_fixture(tmp_path: Path) -> Path:
    """Write a fixture whose first recording stores ``args`` as a string.

    Returns
    -------
    Path
        The path to the written fixture file.
    """
    data = {
        "version": "1.0",
        "metadata": {
            "created_at": "2026-01-15T10:30:00+00:00",
            "cmdmox_version": "0.2.0",
            "platform": "linux",
            "python_version": "3.12.0",
        },
        "recordings": [
            {
                "sequence": 0,
                "command": "git",
                "args": "status",
                "stdin": "",
                "env_subset": {},
                "stdout": "ok\n",
                "stderr": "",
                "exit_code": 0,
                "timestamp": "2026-01-15T10:30:01+00:00",
                "duration_ms": 0,
            }
        ],
        "scrubbing_rules": [],
    }
    path = tmp_path / "corrupt_git_fixture.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@when(
    parsers.cfparse(
        'the command "{cmd}" is spied with that replay fixture expecting an error'
    ),
    target_fixture="replay_error",
)
def spy_with_corrupt_replay_fixture(
    mox: CmdMox,
    cmd: str,
    corrupt_git_fixture_path: Path,
) -> ValueError:
    """Attach a corrupt fixture and capture the schema error.

    Returns
    -------
    ValueError
        The exception raised while attaching the fixture.
    """
    with pytest.raises(ValueError, match="Invalid fixture schema") as excinfo:
        mox.spy(cmd).replay(corrupt_git_fixture_path)
    return excinfo.value


@then(parsers.cfparse('the spy "{cmd}" should not have a replay session attached'))
def spy_has_no_replay_session(mox: CmdMox, cmd: str) -> None:
    """Assert the spy has no attached replay session."""
    assert not mox.spies[cmd].has_replay_session, "Assertion failed"


@then(parsers.cfparse('the invocation error message should contain "{text}"'))
def invocation_error_contains(
    invocation_error: UnexpectedCommandError, text: str
) -> None:
    """Assert the captured invocation error contains *text*."""
    assert text in str(invocation_error), "Assertion failed"
