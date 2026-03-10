"""Step definitions for replay session BDD scenarios."""

from __future__ import annotations

import dataclasses as dc
import typing as t

import pytest
from pytest_bdd import given, parsers, then, when

from cmd_mox.errors import VerificationError
from cmd_mox.ipc import Invocation, Response
from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation
from cmd_mox.record.replay import ReplaySession

if t.TYPE_CHECKING:
    from pathlib import Path


@dc.dataclass
class RecordingSpec:
    """Optional overrides for building a RecordedInvocation."""

    sequence: int = 0
    stdin: str = ""
    env_subset: dict[str, str] = dc.field(default_factory=dict)


def _build_recording(
    command: str,
    args: list[str],
    spec: RecordingSpec | None = None,
) -> RecordedInvocation:
    """Build a RecordedInvocation with sensible defaults."""
    s = spec or RecordingSpec()
    return RecordedInvocation(
        sequence=s.sequence,
        command=command,
        args=args,
        stdin=s.stdin,
        env_subset=s.env_subset,
        stdout="ok\n",
        stderr="",
        exit_code=0,
        timestamp="2026-01-15T10:30:00+00:00",
        duration_ms=0,
    )


def _save_fixture(
    tmp_path: Path,
    recordings: list[RecordedInvocation],
) -> Path:
    """Persist a fixture file and return its path."""
    fixture = FixtureFile(
        version=FixtureFile.SCHEMA_VERSION,
        metadata=FixtureMetadata.create(),
        recordings=recordings,
        scrubbing_rules=[],
    )
    path = tmp_path / "replay_fixture.json"
    fixture.save(path)
    return path


# -- Given steps --------------------------------------------------------------


@given(
    parsers.parse('a fixture file with a recording of "{cmd}" with args "{args}"'),
    target_fixture="replay_fixture_path",
)
def fixture_with_single_recording(tmp_path: Path, cmd: str, args: str) -> Path:
    """Create a fixture file with a single recording."""
    rec = _build_recording(cmd, args.split())
    return _save_fixture(tmp_path, [rec])


@given(
    parsers.parse(
        'a fixture file with {count:d} recordings of "{cmd}" with args "{args}"'
    ),
    target_fixture="replay_fixture_path",
)
def fixture_with_n_recordings(tmp_path: Path, count: int, cmd: str, args: str) -> Path:
    """Create a fixture file with *count* identical recordings."""
    recs = [
        _build_recording(cmd, args.split(), RecordingSpec(sequence=i))
        for i in range(count)
    ]
    return _save_fixture(tmp_path, recs)


@given(
    parsers.parse(
        'a fixture file with a recording of "{invocation}"'
        ' and stdin "{stdin}" and env "{env_kv}"'
    ),
    target_fixture="replay_fixture_path",
)
def fixture_with_stdin_and_env(
    tmp_path: Path,
    invocation: str,
    stdin: str,
    env_kv: str,
) -> Path:
    """Create a fixture file with specific stdin and env_subset."""
    cmd, *args = invocation.split()
    key, _, value = env_kv.partition("=")
    rec = _build_recording(
        cmd, args, RecordingSpec(stdin=stdin, env_subset={key: value})
    )
    return _save_fixture(tmp_path, [rec])


@given(
    "a replay session targeting that fixture in strict mode",
    target_fixture="replay_session",
)
def replay_session_strict(replay_fixture_path: Path) -> ReplaySession:
    """Create a strict-mode ReplaySession."""
    return ReplaySession(replay_fixture_path, strict_matching=True)


@given(
    "a replay session targeting that fixture in fuzzy mode",
    target_fixture="replay_session",
)
def replay_session_fuzzy(replay_fixture_path: Path) -> ReplaySession:
    """Create a fuzzy-mode ReplaySession."""
    return ReplaySession(replay_fixture_path, strict_matching=False)


# -- When steps ---------------------------------------------------------------


@when("the replay session is loaded")
def load_replay_session(replay_session: ReplaySession) -> None:
    """Load the fixture into the replay session."""
    replay_session.load()


@when(
    parsers.parse('a replay invocation of "{cmd}" with args "{args}" is matched'),
    target_fixture="replay_match_result",
)
def match_replay_invocation(
    replay_session: ReplaySession, cmd: str, args: str
) -> Response | None:
    """Match an invocation against the replay session."""
    inv = Invocation(command=cmd, args=args.split(), stdin="", env={})
    return replay_session.match(inv)


@when(
    parsers.parse('a replay invocation of "{cmd}" with args "{args}" is matched again'),
    target_fixture="replay_match_result",
)
def match_replay_invocation_again(
    replay_session: ReplaySession, cmd: str, args: str
) -> Response | None:
    """Match a second invocation against the replay session."""
    inv = Invocation(command=cmd, args=args.split(), stdin="", env={})
    return replay_session.match(inv)


@when(
    parsers.parse(
        'a replay invocation of "{cmd}" with args "{args}"'
        " with different stdin and env is matched"
    ),
    target_fixture="replay_match_result",
)
def match_replay_with_different_stdin_env(
    replay_session: ReplaySession, cmd: str, args: str
) -> Response | None:
    """Match an invocation with deliberately different stdin and env."""
    inv = Invocation(
        command=cmd,
        args=args.split(),
        stdin="completely different",
        env={"OTHER_KEY": "other_value"},
    )
    return replay_session.match(inv)


# -- Then steps ---------------------------------------------------------------


@then(parsers.parse('the replay match result is a response with stdout "{stdout}"'))
def replay_result_has_stdout(replay_match_result: Response | None, stdout: str) -> None:
    """Assert the match result is a Response with expected stdout."""
    assert replay_match_result is not None
    assert isinstance(replay_match_result, Response)
    # Gherkin passes escape sequences as literal characters.
    expected = stdout.encode("utf-8").decode("unicode_escape")
    assert replay_match_result.stdout == expected


@then("the replay match result is None")
def replay_result_is_none(replay_match_result: Response | None) -> None:
    """Assert the match result is None."""
    assert replay_match_result is None


@then("all replay recordings are consumed")
def all_recordings_consumed(replay_session: ReplaySession) -> None:
    """Assert that all recording indices are in the consumed set."""
    assert replay_session._fixture is not None
    expected = set(range(len(replay_session._fixture.recordings)))
    assert replay_session._consumed == expected


@then("replay verify_all_consumed does not raise")
def verify_does_not_raise(replay_session: ReplaySession) -> None:
    """Assert that verify_all_consumed() completes without error."""
    replay_session.verify_all_consumed()


@then("replay verify_all_consumed raises VerificationError")
def verify_raises(replay_session: ReplaySession) -> None:
    """Assert that verify_all_consumed() raises VerificationError."""
    with pytest.raises(VerificationError):
        replay_session.verify_all_consumed()
