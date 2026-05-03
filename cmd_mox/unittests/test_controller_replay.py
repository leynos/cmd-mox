"""Unit tests for controller replay integration in ``_make_response()``."""

from __future__ import annotations

import datetime as dt
import typing as t

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.errors import UnexpectedCommandError, VerificationError
from cmd_mox.ipc import Invocation, Response
from cmd_mox.record.fixture import RecordedInvocation
from cmd_mox.record.replay import ReplaySession
from tests.helpers.fixtures import write_minimal_replay_fixture, write_replay_fixture

if t.TYPE_CHECKING:
    from pathlib import Path


class TestControllerReplayIntegration:
    """Replay-backed controller dispatch should use fixture responses."""

    def test_replay_match_uses_fixture_response_before_static_fallback(
        self, tmp_path: Path
    ) -> None:
        """A replay match should win over a spy's configured static response."""
        mox = CmdMox()
        fixture_path = write_minimal_replay_fixture(tmp_path)
        spy = mox.spy("git").returns(stdout="fallback").replay(fixture_path)
        invocation = Invocation(command="git", args=["status"], stdin="", env={})

        response = mox._make_response(invocation)

        assert response.stdout == "ok\n"
        assert invocation.stdout == "ok\n"
        assert spy.invocations == [invocation]

    def test_replay_match_bypasses_dynamic_handler(self, tmp_path: Path) -> None:
        """A replay match should not call the spy's dynamic handler."""
        mox = CmdMox()
        fixture_path = write_minimal_replay_fixture(tmp_path)
        handler_calls: list[Invocation] = []

        def handler(invocation: Invocation) -> Response:
            handler_calls.append(invocation)
            return Response(stdout="handler", stderr="", exit_code=0)

        mox.spy("git").runs(handler).replay(fixture_path)
        invocation = Invocation(command="git", args=["status"], stdin="", env={})

        response = mox._make_response(invocation)

        assert response.stdout == "ok\n"
        assert handler_calls == []

    def test_replay_match_applies_expectation_env_to_invocation_and_response(
        self, tmp_path: Path
    ) -> None:
        """Replay matches should still apply expectation env semantics."""
        mox = CmdMox()
        fixture_path = write_minimal_replay_fixture(tmp_path)
        spy = mox.spy("git").with_env({"EXPECT_ENV": "VALUE"}).replay(fixture_path)
        invocation = Invocation(command="git", args=["status"], stdin="", env={})

        response = mox._handle_invocation(invocation)

        assert response.stdout == "ok\n"
        assert response.env["EXPECT_ENV"] == "VALUE"
        assert invocation.env["EXPECT_ENV"] == "VALUE"
        assert spy.invocations == [invocation]
        assert list(mox.journal) == [invocation]

    def test_replay_match_rejects_conflicting_expectation_env(
        self, tmp_path: Path
    ) -> None:
        """Replay matches should reject conflicting invocation environment."""
        mox = CmdMox()
        fixture_path = write_minimal_replay_fixture(tmp_path)
        spy = mox.spy("git").with_env({"EXPECT_ENV": "VALUE"}).replay(fixture_path)
        invocation = Invocation(
            command="git",
            args=["status"],
            stdin="",
            env={"EXPECT_ENV": "DIFF"},
        )

        with pytest.raises(UnexpectedCommandError, match="conflicting environment"):
            mox._handle_invocation(invocation)

        assert spy.invocations == []
        assert len(mox.journal) == 0

    def test_handle_invocation_records_replay_match_in_spy_and_journal(
        self, tmp_path: Path
    ) -> None:
        """Replay-backed responses should still update spy history and journal."""
        mox = CmdMox()
        fixture_path = write_minimal_replay_fixture(tmp_path)
        spy = mox.spy("git").replay(fixture_path)
        invocation = Invocation(command="git", args=["status"], stdin="", env={})

        response = mox._handle_invocation(invocation)

        assert response.stdout == "ok\n"
        assert spy.invocations == [invocation]
        assert list(mox.journal) == [invocation]
        assert spy.call_count == 1

    def test_strict_replay_mismatch_raises_without_recording(
        self, tmp_path: Path
    ) -> None:
        """Strict replay should fail immediately when no fixture entry matches."""
        mox = CmdMox()
        fixture_path = write_minimal_replay_fixture(tmp_path)
        spy = mox.spy("git").returns(stdout="fallback").replay(fixture_path)
        invocation = Invocation(command="git", args=["commit"], stdin="", env={})

        with pytest.raises(UnexpectedCommandError, match="No fixture recording"):
            mox._handle_invocation(invocation)

        assert spy.invocations == []
        assert len(mox.journal) == 0

    def test_fuzzy_replay_mismatch_falls_back_to_spy_response(
        self, tmp_path: Path
    ) -> None:
        """Fuzzy replay should fall back to the spy response when no match exists."""
        mox = CmdMox()
        fixture_path = write_minimal_replay_fixture(tmp_path)
        spy = (
            mox.spy("git").returns(stdout="fallback").replay(fixture_path, strict=False)
        )
        invocation = Invocation(command="git", args=["commit"], stdin="", env={})

        response = mox._handle_invocation(invocation)

        assert response.stdout == "fallback"
        assert spy.invocations == [invocation]
        assert list(mox.journal) == [invocation]

    def test_fuzzy_replay_mismatch_falls_back_to_dynamic_handler(
        self, tmp_path: Path
    ) -> None:
        """Fuzzy replay should fall back to the spy handler when no match exists."""
        mox = CmdMox()
        fixture_path = write_minimal_replay_fixture(tmp_path)
        handler_calls: list[Invocation] = []

        def handler(invocation: Invocation) -> Response:
            handler_calls.append(invocation)
            return Response(stdout="handler-fallback", stderr="", exit_code=0)

        spy = mox.spy("git").runs(handler).replay(fixture_path, strict=False)
        invocation = Invocation(command="git", args=["commit"], stdin="", env={})

        response = mox._handle_invocation(invocation)

        assert handler_calls == [invocation]
        assert response.stdout == "handler-fallback"
        assert spy.invocations == [invocation]
        assert list(mox.journal) == [invocation]


class TestControllerReplayVerification:
    """Replay-backed controller verification should enforce fixture consumption."""

    def test_verify_raises_when_replay_fixture_has_unconsumed_recordings(
        self, tmp_path: Path
    ) -> None:
        """Controller verification should fail when replay entries remain unused."""
        mox = CmdMox()
        fixture_path = write_replay_fixture(
            tmp_path,
            [
                RecordedInvocation(
                    sequence=0,
                    command="git",
                    args=["status"],
                    stdin="",
                    env_subset={},
                    stdout="ok\n",
                    stderr="",
                    exit_code=0,
                    timestamp=dt.datetime.now(dt.UTC).isoformat(),
                    duration_ms=0,
                ),
                RecordedInvocation(
                    sequence=1,
                    command="git",
                    args=["commit"],
                    stdin="",
                    env_subset={},
                    stdout="committed\n",
                    stderr="",
                    exit_code=0,
                    timestamp=dt.datetime.now(dt.UTC).isoformat(),
                    duration_ms=1,
                ),
            ],
        )
        mox.spy("git").replay(fixture_path)
        invocation = Invocation(command="git", args=["status"], stdin="", env={})

        with mox:
            mox.replay()
            mox._handle_invocation(invocation)
            with pytest.raises(
                VerificationError,
                match="Not all fixture recordings were consumed during replay",
            ):
                mox.verify()

    def test_verify_passes_when_replay_fixture_is_fully_consumed(
        self, tmp_path: Path
    ) -> None:
        """Controller verification should succeed once all replay entries are used."""
        mox = CmdMox()
        fixture_path = write_minimal_replay_fixture(tmp_path)
        mox.spy("git").replay(fixture_path)
        invocation = Invocation(command="git", args=["status"], stdin="", env={})

        with mox:
            mox.replay()
            mox._handle_invocation(invocation)
            mox.verify()

    def test_verify_skips_unconsumed_check_when_replay_allows_unmatched(
        self, tmp_path: Path
    ) -> None:
        """Controllers should respect direct ReplaySession allow_unmatched opt-out."""
        mox = CmdMox()
        spy = mox.spy("git")
        fixture_path = write_minimal_replay_fixture(tmp_path)
        replay_session = ReplaySession(fixture_path, allow_unmatched=True)
        replay_session.load()
        # The fluent API does not expose allow_unmatched; inject the direct
        # session to assert controller verification honours the lower-level opt-out.
        spy._replay_session = replay_session

        with mox:
            mox.replay()
            mox.verify()
