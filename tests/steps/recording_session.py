"""Step definitions for recording session BDD scenarios."""

from __future__ import annotations

import sys
import typing as t

from pytest_bdd import given, parsers, then, when

from cmd_mox.ipc import Invocation, Response
from cmd_mox.record.session import RecordingSession

if t.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox.record.fixture import FixtureFile


@given(
    "a recording session targeting a temporary fixture file",
    target_fixture="session",
)
def recording_session_targeting_tmp(tmp_path: Path) -> RecordingSession:
    """Create a recording session writing to a temporary fixture file."""
    return RecordingSession(tmp_path / "fixture.json")


@given(
    parsers.parse('a recording session with allowlist "{key}"'),
    target_fixture="session",
)
def recording_session_with_allowlist(tmp_path: Path, key: str) -> RecordingSession:
    """Create a recording session with the given key on its allowlist."""
    return RecordingSession(
        tmp_path / "fixture.json",
        env_allowlist=[key],
    )


@when("the session is started")
def start_session(session: RecordingSession) -> None:
    """Start the recording session."""
    session.start()


@when(parsers.parse('an invocation of "{cmd}" with args "{args}" is recorded'))
def record_invocation(session: RecordingSession, cmd: str, args: str) -> None:
    """Record a synthetic invocation with the given command and args."""
    invocation = Invocation(
        command=cmd,
        args=args.split(),
        stdin="",
        env={"SAFE_VAR": "value"},
    )
    response = Response(stdout="output\n", stderr="", exit_code=0)
    session.record(invocation, response)


@when("an invocation with sensitive and system env vars is recorded")
def record_invocation_with_sensitive_env(session: RecordingSession) -> None:
    """Record an invocation carrying sensitive and system env vars."""
    invocation = Invocation(
        command="test",
        args=[],
        stdin="",
        env={
            "SECRET_TOKEN": "s3cret",
            "PATH": "/usr/bin",
            "MY_SETTING": "enabled",
            "SAFE_VAR": "keep",
        },
    )
    response = Response(stdout="", stderr="", exit_code=0)
    session.record(invocation, response)


@when("the session is finalized", target_fixture="fixture")
def finalize_session(session: RecordingSession) -> FixtureFile:
    """Finalize the session and return the resulting fixture."""
    return session.finalize()


@then("the fixture file exists on disk")
def fixture_file_exists(session: RecordingSession) -> None:
    """Assert that the fixture file was written to disk."""
    assert session._fixture_path.exists()


@then(parsers.parse("the fixture contains {count:d} recording"))
def fixture_has_n_recordings(fixture: FixtureFile, count: int) -> None:
    """Assert the fixture contains exactly *count* recordings."""
    assert len(fixture.recordings) == count


@then(parsers.parse('the recording command is "{cmd}"'))
def recording_command_is(fixture: FixtureFile, cmd: str) -> None:
    """Assert the first recording's command matches *cmd*."""
    assert fixture.recordings[0].command == cmd


@then(parsers.parse('the recording args are "{args}"'))
def recording_args_are(fixture: FixtureFile, args: str) -> None:
    """Assert the first recording's args match *args*."""
    assert fixture.recordings[0].args == args.split()


@then(parsers.parse('the fixture env_subset does not contain "{key}"'))
def env_subset_excludes(fixture: FixtureFile, key: str) -> None:
    """Assert the first recording's env_subset excludes *key*."""
    assert key not in fixture.recordings[0].env_subset


@then(parsers.parse('the fixture env_subset contains "{key}"'))
def env_subset_includes(fixture: FixtureFile, key: str) -> None:
    """Assert the first recording's env_subset includes *key*."""
    assert key in fixture.recordings[0].env_subset


@then("the fixture metadata contains the current platform")
def metadata_has_platform(fixture: FixtureFile) -> None:
    """Assert fixture metadata platform matches the current platform."""
    assert fixture.metadata.platform == sys.platform


@then("the fixture metadata contains a valid ISO8601 timestamp")
def metadata_has_timestamp(fixture: FixtureFile) -> None:
    """Assert fixture metadata contains a non-empty ISO8601 timestamp."""
    assert fixture.metadata.created_at
    assert "T" in fixture.metadata.created_at


@then("the fixture metadata contains the Python version")
def metadata_has_python_version(fixture: FixtureFile) -> None:
    """Assert fixture metadata python_version matches the current runtime."""
    assert fixture.metadata.python_version == sys.version
