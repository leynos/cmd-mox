"""State-machine test for ReplaySession consumption tracking (CONS-1).

A ``RuleBasedStateMachine`` drives a loaded session through generated
sequences of matches and verifications, comparing it after every step with a
model that applies the independent oracle from ``_replay_helpers``. The model
is what makes the test meaningful: it can predict exhaustion, partial
verification, and the exact unconsumed indices without consulting the session.
"""

from __future__ import annotations

import re
import tempfile
import typing as typ

import hypothesis.strategies as st
import pytest
from hypothesis import settings
from hypothesis.stateful import (
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from cmd_mox.errors import VerificationError
from cmd_mox.record.fixture import RecordedInvocation
from cmd_mox.record.replay import ReplaySession
from cmd_mox.unittests._replay_helpers import (
    ReplayScenario,
    make_invocation,
    make_recorded_invocation,
    oracle_find_match,
    write_fixture_file,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from cmd_mox.ipc import Invocation, Response

#: One line of an ``verify_all_consumed()`` failure message.
_INDEX_LINE = re.compile(r"^\s*\[(\d+)\]", re.MULTILINE)

_MACHINE_COMMANDS = ("aa", "bb")
_MACHINE_ARGS = ("x", "y", "z")
_MACHINE_ENV_KEYS = ("K", "L")
_MACHINE_ENV_VALUES = ("v", "w")
_MACHINE_STDIN = ("", "s")
_TIMESTAMP = "2026-01-15T10:30:00+00:00"


def _machine_recordings() -> st.SearchStrategy[RecordedInvocation]:
    """Generate recordings from small alphabets so duplicates are common."""
    return st.builds(
        RecordedInvocation,
        sequence=st.integers(min_value=0, max_value=2),
        command=st.sampled_from(_MACHINE_COMMANDS),
        args=st.lists(st.sampled_from(_MACHINE_ARGS), max_size=2),
        stdin=st.sampled_from(_MACHINE_STDIN),
        env_subset=st.dictionaries(
            st.sampled_from(_MACHINE_ENV_KEYS),
            st.sampled_from(_MACHINE_ENV_VALUES),
            max_size=2,
        ),
        stdout=st.sampled_from(("ok\n", "other\n")),
        stderr=st.just(""),
        exit_code=st.sampled_from((0, 1)),
        timestamp=st.just(_TIMESTAMP),
        duration_ms=st.just(0),
    )


class _ForgetfulReplaySession(ReplaySession):
    """A session that answers matches without recording consumption."""

    def match(self, invocation: Invocation) -> Response | None:
        """Answer as usual, then discard the consumption bookkeeping."""
        response = super().match(invocation)
        self._consumed.clear()
        return response


class ReplaySessionMachine(RuleBasedStateMachine):
    """Compare a live session with an oracle-driven consumption model."""

    #: Swappable so a scripted test can supply a broken session.
    session_type: type[ReplaySession] = ReplaySession

    def __init__(self) -> None:
        """Create the machine with no session loaded."""
        super().__init__()
        self._directory = tempfile.TemporaryDirectory()
        self.session: ReplaySession | None = None
        self.recordings: tuple[RecordedInvocation, ...] = ()
        self.consumed: set[int] = set()
        self.strict = True
        self.allow_unmatched = False

    def teardown(self) -> None:
        """Remove the fixture directory once the run finishes."""
        self._directory.cleanup()

    # -- model operations, called by the rules and by scripted tests --------

    def load_session(
        self,
        *,
        strict: bool,
        allow_unmatched: bool,
        recordings: cabc.Sequence[RecordedInvocation],
    ) -> None:
        """Write the fixture, load a session, and reset the model."""
        path = write_fixture_file(self._fixture_dir(), recordings)
        self.session = self.session_type(
            path,
            strict_matching=strict,
            allow_unmatched=allow_unmatched,
        )
        self.session.load()
        self.recordings = tuple(recordings)
        self.consumed = set()
        self.strict = strict
        self.allow_unmatched = allow_unmatched
        self.assert_model_consistent()

    def match_and_assert(self, invocation: Invocation) -> Response | None:
        """Match once, compare with the oracle, and update the model.

        Returns
        -------
        Response or None
            Whatever the session returned.
        """
        session = self._require_session()
        scenario = ReplayScenario(
            strict=self.strict,
            invocation=invocation,
            recordings=self.recordings,
            consumed=frozenset(self.consumed),
        )
        expected = oracle_find_match(scenario)
        before = session.consumed_count
        response = session.match(invocation)

        if expected is None:
            assert response is None, f"unexpected match for {invocation!r}"
            assert session.consumed_count == before, "consumption changed on a miss"
        else:
            assert response is not None, f"expected recording {expected}"
            recording = self.recordings[expected]
            assert (
                response.stdout,
                response.stderr,
                response.exit_code,
            ) == (recording.stdout, recording.stderr, recording.exit_code), (
                f"response did not come from recording {expected}"
            )
            assert session.consumed_count == before + 1, (
                "a match must consume exactly one more recording"
            )
            self.consumed.add(expected)

        self.assert_model_consistent()
        return response

    def verify_and_assert(self) -> None:
        """Verify once, comparing the outcome and message with the model."""
        session = self._require_session()
        expected_missing = sorted(set(range(len(self.recordings))) - self.consumed)
        should_raise = bool(expected_missing) and not self.allow_unmatched
        before = session.consumed_count
        try:
            session.verify_all_consumed()
        except VerificationError as exc:
            assert should_raise, "verification raised with nothing unconsumed"
            reported = [int(match.group(1)) for match in _INDEX_LINE.finditer(str(exc))]
            assert reported == expected_missing, f"reported {reported}"
        else:
            assert not should_raise, f"verification passed with {expected_missing}"
        assert session.consumed_count == before, "verification changed consumption"

    def assert_model_consistent(self) -> None:
        """Check the session's consumption bookkeeping against the model."""
        session = self._require_session()
        assert session.consumed_count == len(self.consumed), (
            f"consumed_count {session.consumed_count} != model {len(self.consumed)}"
        )
        for index in range(len(self.recordings)):
            assert session.is_consumed(index) == (index in self.consumed), (
                f"is_consumed({index}) disagrees with the model"
            )

    # -- rules ---------------------------------------------------------------

    @initialize(
        strict=st.booleans(),
        allow_unmatched=st.booleans(),
        recordings=st.lists(_machine_recordings(), max_size=6),
    )
    def start(
        self,
        *,
        strict: bool,
        allow_unmatched: bool,
        recordings: list[RecordedInvocation],
    ) -> None:
        """Load a fixture drawn by Hypothesis."""
        self.load_session(
            strict=strict,
            allow_unmatched=allow_unmatched,
            recordings=recordings,
        )

    @precondition(lambda self: bool(self.recordings))
    @rule(
        index=st.integers(min_value=0, max_value=5),
        perturb_stdin=st.booleans(),
        perturb_env=st.booleans(),
    )
    def match_recorded(
        self,
        index: int,
        *,
        perturb_stdin: bool,
        perturb_env: bool,
    ) -> None:
        """Replay a recorded invocation, optionally perturbed."""
        if index >= len(self.recordings):
            return
        recording = self.recordings[index]
        stdin = f"{recording.stdin}other" if perturb_stdin else recording.stdin
        if perturb_env:
            env = {key: f"{value}other" for key, value in recording.env_subset.items()}
        else:
            env = dict(recording.env_subset)
        self.match_and_assert(
            make_invocation(
                recording.command,
                list(recording.args),
                stdin=stdin,
                env=env,
            )
        )

    @rule(
        command=st.sampled_from(_MACHINE_COMMANDS),
        args=st.lists(st.sampled_from(_MACHINE_ARGS), max_size=2),
    )
    def match_arbitrary(self, command: str, args: list[str]) -> None:
        """Replay an invocation that may not have been recorded."""
        self.match_and_assert(make_invocation(command, args))

    @rule()
    def verify(self) -> None:
        """Verify consumption without consuming anything."""
        self.verify_and_assert()

    @invariant()
    def model_holds(self) -> None:
        """Keep the model and the session in step after every rule."""
        if self.session is not None:
            self.assert_model_consistent()

    # -- helpers -------------------------------------------------------------

    def _fixture_dir(self) -> Path:
        """Return the run's temporary directory as a ``Path``."""
        from pathlib import Path as _Path

        return _Path(self._directory.name)

    def _require_session(self) -> ReplaySession:
        """Return the loaded session, failing when none is loaded."""
        if self.session is None:
            msg = "session not loaded"
            raise AssertionError(msg)
        return self.session


TestReplaySessionMachine = ReplaySessionMachine.TestCase
TestReplaySessionMachine.settings = settings(
    max_examples=50,
    stateful_step_count=25,
    deadline=None,
)


def test_scripted_run_reaches_exhaustion_and_complete_verification() -> None:
    """A fixed sequence demonstrates partial verify, exhaustion, and success."""
    machine = ReplaySessionMachine()
    try:
        machine.load_session(
            strict=True,
            allow_unmatched=False,
            recordings=[make_recorded_invocation(), make_recorded_invocation()],
        )
        machine.match_and_assert(make_invocation())
        machine.verify_and_assert()  # partially consumed: model expects an error
        machine.match_and_assert(make_invocation())
        machine.match_and_assert(make_invocation())  # exhausted: returns None
        machine.verify_and_assert()  # fully consumed: passes
    finally:
        machine.teardown()


def test_scripted_run_allows_unmatched_when_configured() -> None:
    """``allow_unmatched`` suppresses the error without changing bookkeeping."""
    machine = ReplaySessionMachine()
    try:
        machine.load_session(
            strict=False,
            allow_unmatched=True,
            recordings=[make_recorded_invocation()],
        )
        machine.verify_and_assert()
        machine.match_and_assert(make_invocation())
        machine.verify_and_assert()
    finally:
        machine.teardown()


def test_a_session_that_forgets_consumption_fails_the_invariant() -> None:
    """The invariant can fail: a non-tracking session trips it."""
    machine = ReplaySessionMachine()
    machine.session_type = _ForgetfulReplaySession
    try:
        machine.load_session(
            strict=True,
            allow_unmatched=False,
            recordings=[make_recorded_invocation()],
        )
        with pytest.raises(AssertionError, match="consumed_count"):
            machine.match_and_assert(make_invocation())
    finally:
        machine.teardown()
