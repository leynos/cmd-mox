"""Step definitions for CommandDouble.record() BDD scenarios."""

from __future__ import annotations

import typing as t

import pytest
from pytest_bdd import given, then, when

from cmd_mox.controller import CmdMox

if t.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox.test_doubles import CommandDouble


@given("a CmdMox controller", target_fixture="mox")
def create_controller() -> CmdMox:
    """Create a fresh CmdMox controller."""
    return CmdMox()


@given('a spy for "git" with passthrough enabled', target_fixture="spy")
def spy_with_passthrough(mox: CmdMox) -> CommandDouble:
    """Create a spy with passthrough mode enabled."""
    return mox.spy("git").passthrough()


@given('a spy for "git" without passthrough', target_fixture="spy")
def spy_without_passthrough(mox: CmdMox) -> CommandDouble:
    """Create a spy without passthrough mode."""
    return mox.spy("git")


@when("record is called with a fixture path", target_fixture="spy_after_record")
def call_record(spy: CommandDouble, tmp_path: Path) -> CommandDouble:
    """Call record() with a temporary fixture path."""
    return spy.record(tmp_path / "fixture.json")


@when("record is called without passthrough it raises ValueError")
def call_record_raises(spy: CommandDouble, tmp_path: Path) -> None:
    """Attempt to call record() and verify it raises ValueError."""
    with pytest.raises(ValueError, match=r"record.*requires passthrough"):
        spy.record(tmp_path / "fixture.json")


@then("the spy has a recording session attached")
def spy_has_session(spy_after_record: CommandDouble) -> None:
    """Assert the spy has a recording session attached."""
    assert spy_after_record.has_recording_session is True


@then("the recording session is started")
def session_is_started(spy_after_record: CommandDouble) -> None:
    """Assert the recording session has been started."""
    assert spy_after_record.has_recording_session is True
    assert spy_after_record._recording_session is not None
    assert spy_after_record._recording_session.is_started is True
