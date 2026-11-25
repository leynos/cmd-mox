"""Step definitions for recording session BDD scenarios."""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import errno
import importlib
import json
import stat
import sys
import typing as typ

import pytest
from pytest_bdd import given, parsers, then, when

from cmd_mox.ipc import Invocation, Response
from cmd_mox.record.fixture import (
    FixtureFile,
    FixtureMetadata,
    RecordedInvocation,
)
from cmd_mox.record.session import RecordingSession

if typ.TYPE_CHECKING:
    from pathlib import Path


@given(
    "a recording session targeting a temporary fixture file",
    target_fixture="session",
)
def recording_session_targeting_tmp(tmp_path: Path) -> RecordingSession:
    """Create a recording session writing to a temporary fixture file.

    Returns
    -------
    RecordingSession
        A session configured to write to the temporary fixture file.
    """
    return RecordingSession(tmp_path / "fixture.json")


@given(
    parsers.parse('a recording session with allowlist "{key}"'),
    target_fixture="session",
)
def recording_session_with_allowlist(tmp_path: Path, key: str) -> RecordingSession:
    """Create a recording session with the given key on its allowlist.

    Returns
    -------
    RecordingSession
        A session whose environment allowlist contains *key*.
    """
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
    """Finalize the session and return the resulting fixture.

    Returns
    -------
    FixtureFile
        The finalised fixture written by the session.
    """
    return session.finalize()


@then("the fixture file exists on disk")
def fixture_file_exists(session: RecordingSession) -> None:
    """Assert that the fixture file was written to disk."""
    assert session.fixture_path.exists(), "Assertion failed"


@then(parsers.re(r"the fixture contains (?P<count>\d+) recordings?"))
def fixture_has_n_recordings(fixture: FixtureFile, count: str) -> None:
    """Assert the fixture contains exactly *count* recordings."""
    assert len(fixture.recordings) == int(count), "Assertion failed"


@then(parsers.parse('the recording command is "{cmd}"'))
def recording_command_is(fixture: FixtureFile, cmd: str) -> None:
    """Assert the first recording's command matches *cmd*."""
    assert fixture.recordings[0].command == cmd, "Assertion failed"


@then(parsers.parse('the recording args are "{args}"'))
def recording_args_are(fixture: FixtureFile, args: str) -> None:
    """Assert the first recording's args match *args*."""
    assert fixture.recordings[0].args == args.split(), "Assertion failed"


@then(parsers.parse('the fixture env_subset does not contain "{key}"'))
def env_subset_excludes(fixture: FixtureFile, key: str) -> None:
    """Assert the first recording's env_subset excludes *key*."""
    assert key not in fixture.recordings[0].env_subset, "Assertion failed"


@then(parsers.parse('the fixture env_subset contains "{key}"'))
def env_subset_includes(fixture: FixtureFile, key: str) -> None:
    """Assert the first recording's env_subset includes *key*."""
    assert key in fixture.recordings[0].env_subset, "Assertion failed"


@then("the fixture metadata contains the current platform")
def metadata_has_platform(fixture: FixtureFile) -> None:
    """Assert fixture metadata platform matches the current platform."""
    assert fixture.metadata.platform == sys.platform, "Assertion failed"


@then("the fixture metadata contains a valid ISO8601 timestamp")
def metadata_has_timestamp(fixture: FixtureFile) -> None:
    """Assert fixture metadata contains a valid, parseable ISO8601 timestamp."""
    assert fixture.metadata.created_at, "Assertion failed"
    parsed = dt.datetime.fromisoformat(fixture.metadata.created_at)
    assert isinstance(parsed, dt.datetime), "Assertion failed"


@then("the fixture metadata contains the Python version")
def metadata_has_python_version(fixture: FixtureFile) -> None:
    """Assert fixture metadata python_version matches the current runtime."""
    assert fixture.metadata.python_version == sys.version, "Assertion failed"


# -- Steps for fixture migration scenario ------------------------------------


@given("a 0.9.0 fixture file on disk", target_fixture="fixture_path")
def v09_fixture_on_disk(tmp_path: Path) -> Path:
    """Write a 0.9.0 fixture file to disk for migration testing.

    Returns
    -------
    Path
        The path to the written legacy fixture file.
    """
    data = {
        "version": "0.9.0",
        "metadata": {
            "created_at": "2025-01-15T10:30:00Z",
            "cmdmox_version": "0.1.0",
            "platform": sys.platform,
            "python_version": sys.version,
        },
        "recordings": [
            {
                "sequence": 0,
                "command": "echo",
                "args": ["hello"],
                "stdin": "",
                "env_subset": {},
                "stdout": "hello\n",
                "stderr": "",
                "exit_code": 0,
                "timestamp": "2025-01-15T10:30:01Z",
                "duration_ms": 5,
            },
        ],
        "scrubbing_rules": [],
    }
    path = tmp_path / "old_fixture.json"
    path.write_text(json.dumps(data, indent=2) + "\n")
    return path


@when("the fixture file is loaded", target_fixture="loaded_fixture")
def load_fixture_file(fixture_path: Path) -> FixtureFile:
    """Load the fixture file from disk.

    Returns
    -------
    FixtureFile
        The fixture loaded from *fixture_path*.
    """
    return FixtureFile.load(fixture_path)


@then(parsers.parse('the loaded fixture has version "{version}"'))
def loaded_fixture_has_version(loaded_fixture: FixtureFile, version: str) -> None:
    """Assert the loaded fixture's version matches."""
    assert loaded_fixture.version == version, "Assertion failed"


@then(parsers.re(r"the loaded fixture contains (?P<count>\d+) recordings?"))
def loaded_fixture_has_n_recordings(loaded_fixture: FixtureFile, count: str) -> None:
    """Assert the loaded fixture contains exactly *count* recordings."""
    assert len(loaded_fixture.recordings) == int(count), "Assertion failed"


@then("the loaded fixture metadata is preserved")
def loaded_fixture_metadata_preserved(loaded_fixture: FixtureFile) -> None:
    """Assert key metadata fields survive migration unchanged."""
    assert loaded_fixture.metadata.cmdmox_version == "0.1.0", "Assertion failed"
    assert loaded_fixture.metadata.platform == sys.platform, "Assertion failed"


# -- Steps for atomic fixture persistence scenarios --------------------------


@dc.dataclass(slots=True)
class _FixtureContext:
    """State shared by the atomic fixture-persistence scenarios."""

    path: Path
    session: RecordingSession | None = None
    error: OSError | None = None


def _recorded_invocation(sequence: int) -> RecordedInvocation:
    """Build a deterministic recording for *sequence*."""
    return RecordedInvocation(
        sequence=sequence,
        command="git",
        args=["status"],
        stdin="",
        env_subset={},
        stdout="",
        stderr="",
        exit_code=0,
        timestamp="2025-01-15T10:30:01Z",
        duration_ms=1,
    )


@given(
    parsers.parse('a recording session targeting "{relative}"'),
    target_fixture="fixture_context",
)
def recording_session_targeting_path(tmp_path: Path, relative: str) -> _FixtureContext:
    """Create a recording session writing to *relative* under the temp directory.

    Returns
    -------
    _FixtureContext
        Context holding the session and its destination path.
    """
    path = tmp_path / relative
    return _FixtureContext(path=path, session=RecordingSession(path))


@given(
    parsers.re(
        r'an existing fixture "(?P<relative>[^"]+)" with (?P<count>\d+) recordings?'
    ),
    target_fixture="fixture_context",
)
def existing_fixture_on_disk(
    tmp_path: Path, relative: str, count: str
) -> _FixtureContext:
    """Write an existing fixture with *count* recordings to disk.

    Returns
    -------
    _FixtureContext
        Context holding the destination path of the existing fixture.
    """
    path = tmp_path / relative
    fixture = FixtureFile(
        version=FixtureFile.SCHEMA_VERSION,
        metadata=FixtureMetadata.create(),
        recordings=[_recorded_invocation(index) for index in range(int(count))],
        scrubbing_rules=[],
    )
    fixture.save(path)
    return _FixtureContext(path=path)


@given("the final rename of fixture writes will fail")
def final_rename_will_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the atomic-write rename step fail with a permission error."""
    module = importlib.import_module("cmd_mox._atomic_write")

    def refuse_replace(temporary: Path, target: Path) -> None:
        """Report that the target cannot be replaced."""
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(module, "_replace", refuse_replace)


@when(parsers.parse('the session records a "{cmd}" invocation and finalizes'))
def record_one_invocation_and_finalize(
    fixture_context: _FixtureContext, cmd: str
) -> None:
    """Record a single invocation, then finalize the session."""
    session = fixture_context.session
    assert session is not None, "the scenario must supply a recording session"
    session.start()
    invocation = Invocation(command=cmd, args=[], stdin="", env={})
    response = Response(stdout="output\n", stderr="", exit_code=0)
    session.record(invocation, response)
    session.finalize()


@when(
    parsers.re(
        r"a new session records (?P<count>\d+) invocations?"
        r" to the same fixture and finalizes"
    )
)
def new_session_records_and_finalizes(
    fixture_context: _FixtureContext, count: str
) -> None:
    """Record invocations in a fresh session and finalize, capturing failures."""
    session = RecordingSession(fixture_context.path)
    session.start()
    for index in range(int(count)):
        invocation = Invocation(command="git", args=[f"call-{index}"], stdin="", env={})
        response = Response(stdout="", stderr="", exit_code=0)
        session.record(invocation, response)
    try:
        session.finalize()
    except OSError as exc:
        fixture_context.error = exc


@then("the fixture file loads with 1 recording")
def fixture_file_loads_with_one_recording(fixture_context: _FixtureContext) -> None:
    """Assert the fixture on disk is loadable and holds one recording."""
    loaded = FixtureFile.load(fixture_context.path)
    assert len(loaded.recordings) == 1, "Assertion failed"


@then("the fixture file still loads with 1 recording")
def fixture_file_still_loads_with_one_recording(
    fixture_context: _FixtureContext,
) -> None:
    """Assert the previous fixture survived the failed save."""
    assert fixture_context.error is not None, "finalizing should have failed"
    loaded = FixtureFile.load(fixture_context.path)
    assert len(loaded.recordings) == 1, "Assertion failed"


@then("the fixture file is readable and writable only by its owner")
def fixture_file_is_owner_only(fixture_context: _FixtureContext) -> None:
    """Assert the fixture's POSIX mode is exactly ``0o600``."""
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits are not enforced on Windows")
    mode = stat.S_IMODE(fixture_context.path.stat().st_mode)
    assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


@then("the fixture directory contains only the fixture file")
def fixture_directory_contains_only_fixture(
    fixture_context: _FixtureContext,
) -> None:
    """Assert no temporary or residue file remains beside the fixture."""
    entries = {entry.name for entry in fixture_context.path.parent.iterdir()}
    assert entries == {fixture_context.path.name}, entries


@then(parsers.parse('finalizing fails with an atomic write error at stage "{stage}"'))
def finalizing_fails_at_stage(fixture_context: _FixtureContext, stage: str) -> None:
    """Assert the captured failure is an atomic write error at *stage*."""
    error = fixture_context.error
    assert error is not None, "finalizing should have failed"
    module = importlib.import_module("cmd_mox._atomic_write")
    assert isinstance(error, module.AtomicWriteError), type(error).__name__
    atomic_error = typ.cast("typ.Any", error)
    assert atomic_error.stage == module.AtomicWriteStage(stage), atomic_error.stage
