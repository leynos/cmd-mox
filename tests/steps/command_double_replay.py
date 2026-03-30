"""Step definitions for CommandDouble.replay() BDD scenarios."""

from __future__ import annotations

import typing as t

import pytest
from pytest_bdd import given, then, when

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation
from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation

if t.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox.test_doubles import CommandDouble


def _write_fixture(tmp_path: Path) -> Path:
    """Write a minimal valid replay fixture and return its path."""
    fixture = FixtureFile(
        version=FixtureFile.SCHEMA_VERSION,
        metadata=FixtureMetadata.create(),
        recordings=[
            RecordedInvocation(
                sequence=0,
                command="git",
                args=["status"],
                stdin="",
                env_subset={},
                stdout="ok\n",
                stderr="",
                exit_code=0,
                timestamp="2026-01-15T10:30:00+00:00",
                duration_ms=0,
            )
        ],
        scrubbing_rules=[],
    )
    path = tmp_path / "fixture.json"
    fixture.save(path)
    return path


@given("a CmdMox controller", target_fixture="mox")
def create_controller() -> CmdMox:
    """Create a fresh CmdMox controller."""
    return CmdMox()


@given('a replay fixture for "git"', target_fixture="fixture_path")
def replay_fixture(tmp_path: Path) -> Path:
    """Create a replay fixture file for the git command."""
    return _write_fixture(tmp_path)


@given('a "git" spy with passthrough enabled', target_fixture="spy")
def spy_with_passthrough(mox: CmdMox) -> CommandDouble:
    """Create a spy configured for passthrough mode."""
    return mox.spy("git").passthrough()


@when('replay is called on a "git" spy', target_fixture="spy_after_replay")
def call_replay(mox: CmdMox, fixture_path: Path) -> CommandDouble:
    """Attach a strict replay session to a spy."""
    return mox.spy("git").replay(fixture_path)


@when(
    'replay is called on a "git" spy with strict disabled',
    target_fixture="spy_after_replay",
)
def call_replay_fuzzy(mox: CmdMox, fixture_path: Path) -> CommandDouble:
    """Attach a fuzzy replay session to a spy."""
    return mox.spy("git").replay(fixture_path, strict=False)


@when("replay is combined with passthrough it raises ValueError")
def call_replay_with_passthrough_raises(spy: CommandDouble, fixture_path: Path) -> None:
    """Attempt to combine replay with passthrough and assert rejection."""
    with pytest.raises(ValueError, match=r"replay.*passthrough"):
        spy.replay(fixture_path)


@then("the spy has a replay session attached")
def spy_has_replay_session(spy_after_replay: CommandDouble) -> None:
    """Assert a replay session has been attached."""
    assert spy_after_replay.has_replay_session is True


@then("the replay session is loaded")
def replay_session_is_loaded(spy_after_replay: CommandDouble) -> None:
    """Assert the session is immediately usable after replay()."""
    session = spy_after_replay.replay_session
    assert session is not None
    assert (
        session.match(Invocation(command="git", args=["status"], stdin="", env={}))
        is not None
    )


@then("the replay session uses strict matching")
def replay_session_uses_strict_matching(spy_after_replay: CommandDouble) -> None:
    """Assert strict matching is enabled by default."""
    session = spy_after_replay.replay_session
    assert session is not None
    assert session.strict_matching is True


@then("the replay session uses fuzzy matching")
def replay_session_uses_fuzzy_matching(spy_after_replay: CommandDouble) -> None:
    """Assert fuzzy matching is enabled when strict=False."""
    session = spy_after_replay.replay_session
    assert session is not None
    assert session.strict_matching is False
