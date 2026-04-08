"""Unit tests for controller replay integration in ``_make_response()``."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.ipc import Invocation, Response
from tests.helpers.fixtures import write_minimal_replay_fixture

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
