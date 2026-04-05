"""Step definitions for CommandDouble.replay() BDD scenarios."""

from __future__ import annotations

import typing as t

import pytest
from pytest_bdd import given, then, when

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation
from tests.helpers.fixtures import write_minimal_replay_fixture

if t.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox.test_doubles import CommandDouble


@given("a CmdMox controller", target_fixture="mox")
def create_controller() -> t.Generator[CmdMox, None, None]:
    """Create a fresh CmdMox controller with proper cleanup.

    Yields
    ------
    CmdMox
        A fresh controller instance that will be cleaned up after the test.
    """
    mox = CmdMox()
    try:
        yield mox
    finally:
        # Ensure cleanup even if the test raises
        if mox._entered:
            mox.__exit__(None, None, None)


@given('a replay fixture for "git"', target_fixture="fixture_path")
def replay_fixture(tmp_path: Path) -> Path:
    """Create a replay fixture file for the git command.

    Parameters
    ----------
    tmp_path : Path
        Temporary directory path provided by pytest.

    Returns
    -------
    Path
        Path to the created fixture file.
    """
    return write_minimal_replay_fixture(tmp_path)


@given('a "git" spy with passthrough enabled', target_fixture="spy")
def spy_with_passthrough(mox: CmdMox) -> CommandDouble:
    """Create a spy configured for passthrough mode.

    Parameters
    ----------
    mox : CmdMox
        The controller instance to create the spy from.

    Returns
    -------
    CommandDouble
        A git spy with passthrough enabled.
    """
    return mox.spy("git").passthrough()


@when('replay is called on a "git" spy', target_fixture="spy_after_replay")
def call_replay(mox: CmdMox, fixture_path: Path) -> CommandDouble:
    """Attach a strict replay session to a spy.

    Parameters
    ----------
    mox : CmdMox
        The controller instance to create the spy from.
    fixture_path : Path
        Path to the replay fixture file.

    Returns
    -------
    CommandDouble
        A git spy with a strict replay session attached.
    """
    return mox.spy("git").replay(fixture_path)


@when(
    'replay is called on a "git" spy with strict disabled',
    target_fixture="spy_after_replay",
)
def call_replay_fuzzy(mox: CmdMox, fixture_path: Path) -> CommandDouble:
    """Attach a fuzzy replay session to a spy.

    Parameters
    ----------
    mox : CmdMox
        The controller instance to create the spy from.
    fixture_path : Path
        Path to the replay fixture file.

    Returns
    -------
    CommandDouble
        A git spy with a fuzzy replay session attached.
    """
    return mox.spy("git").replay(fixture_path, strict=False)


@when("replay is combined with passthrough it raises ValueError")
def call_replay_with_passthrough_raises(spy: CommandDouble, fixture_path: Path) -> None:
    """Attempt to combine replay with passthrough and assert rejection.

    Parameters
    ----------
    spy : CommandDouble
        A spy with passthrough already enabled.
    fixture_path : Path
        Path to the replay fixture file.

    Raises
    ------
    ValueError
        When replay is called on a spy with passthrough enabled.
    """
    with pytest.raises(ValueError, match=r"replay.*passthrough"):
        spy.replay(fixture_path)


@then("the spy has a replay session attached")
def spy_has_replay_session(spy_after_replay: CommandDouble) -> None:
    """Assert a replay session has been attached.

    Parameters
    ----------
    spy_after_replay : CommandDouble
        A spy with a replay session.
    """
    assert spy_after_replay.has_replay_session is True


@then("the replay session is loaded")
def replay_session_is_loaded(spy_after_replay: CommandDouble) -> None:
    """Assert the session is immediately usable after replay().

    Parameters
    ----------
    spy_after_replay : CommandDouble
        A spy with a replay session.
    """
    session = spy_after_replay.replay_session
    assert session is not None
    assert (
        session.match(Invocation(command="git", args=["status"], stdin="", env={}))
        is not None
    )


@then("the replay session uses strict matching")
def replay_session_uses_strict_matching(spy_after_replay: CommandDouble) -> None:
    """Assert strict matching is enabled by default.

    Parameters
    ----------
    spy_after_replay : CommandDouble
        A spy with a replay session.
    """
    session = spy_after_replay.replay_session
    assert session is not None
    assert session.strict_matching is True


@then("the replay session uses fuzzy matching")
def replay_session_uses_fuzzy_matching(spy_after_replay: CommandDouble) -> None:
    """Assert fuzzy matching is enabled when strict=False.

    Parameters
    ----------
    spy_after_replay : CommandDouble
        A spy with a replay session configured for fuzzy matching.
    """
    session = spy_after_replay.replay_session
    assert session is not None
    assert session.strict_matching is False
