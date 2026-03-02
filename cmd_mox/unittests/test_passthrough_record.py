"""Unit tests for recording integration in PassthroughCoordinator."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation, PassthroughResult

if t.TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.requires_unix_sockets


class TestPassthroughRecording:
    """Tests for PassthroughCoordinator recording integration."""

    def test_finalize_result_records_to_session(self, tmp_path: Path) -> None:
        """finalize_result() records the invocation when a session is attached."""
        fixture_path = tmp_path / "fixture.json"
        with CmdMox() as mox:
            spy = mox.spy("echo").passthrough().record(fixture_path)
            invocation = Invocation(command="echo", args=["hello"], stdin="", env={})
            response = mox._prepare_passthrough(spy, invocation)
            directive = response.passthrough
            assert directive is not None

            result = PassthroughResult(
                invocation_id=directive.invocation_id,
                stdout="hello\n",
                stderr="",
                exit_code=0,
            )
            mox._handle_passthrough_result(result)

        assert spy._recording_session is not None
        assert len(spy._recording_session._recordings) == 1

    def test_finalize_result_skips_when_no_session(self) -> None:
        """finalize_result() does not fail when no recording session exists."""
        with CmdMox() as mox:
            spy = mox.spy("echo").passthrough()
            invocation = Invocation(command="echo", args=["hi"], stdin="", env={})
            response = mox._prepare_passthrough(spy, invocation)
            directive = response.passthrough
            assert directive is not None

            result = PassthroughResult(
                invocation_id=directive.invocation_id,
                stdout="hi\n",
                stderr="",
                exit_code=0,
            )
            # Should not raise — no recording session attached.
            resp = mox._handle_passthrough_result(result)
            assert resp.stdout == "hi\n"

    def test_recorded_data_is_correct(self, tmp_path: Path) -> None:
        """The recorded invocation has the correct command, args, and output."""
        fixture_path = tmp_path / "fixture.json"
        with CmdMox() as mox:
            spy = mox.spy("git").passthrough().record(fixture_path)
            invocation = Invocation(
                command="git", args=["status", "--short"], stdin="", env={}
            )
            response = mox._prepare_passthrough(spy, invocation)
            directive = response.passthrough
            assert directive is not None

            result = PassthroughResult(
                invocation_id=directive.invocation_id,
                stdout="M file.py\n",
                stderr="",
                exit_code=0,
            )
            mox._handle_passthrough_result(result)

        assert spy._recording_session is not None
        recording = spy._recording_session._recordings[0]
        assert recording.command == "git"
        assert recording.args == ["status", "--short"]
        assert recording.stdout == "M file.py\n"
        assert recording.exit_code == 0
