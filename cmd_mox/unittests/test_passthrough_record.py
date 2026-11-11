"""Unit tests for recording integration in PassthroughCoordinator."""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation, PassthroughRequest, PassthroughResult
from cmd_mox.record.fixture import FixtureFile

if typ.TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.requires_unix_sockets


def _complete_passthrough(mox: CmdMox, directive: PassthroughRequest) -> None:
    """Finalise *directive* with an empty, successful passthrough result."""
    mox._handle_passthrough_result(
        PassthroughResult(
            invocation_id=directive.invocation_id,
            stdout="",
            stderr="",
            exit_code=0,
        )
    )


def _record_env_subset(
    tmp_path: Path,
    *,
    command: str,
    with_env: dict[str, str] | None = None,
    invocation_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Record one passthrough and return the resulting ``env_subset``."""
    fixture_path = tmp_path / "fixture.json"
    with CmdMox() as mox:
        spy = mox.spy(command)
        if with_env:
            spy = spy.with_env(with_env)
        spy = spy.passthrough().record(fixture_path)
        invocation = Invocation(
            command=command, args=["status"], stdin="", env=invocation_env or {}
        )
        response = mox._prepare_passthrough(spy, invocation)
        directive = response.passthrough
        assert directive is not None

        _complete_passthrough(mox, directive)

    assert spy._recording_session is not None
    return spy._recording_session._recordings[0].env_subset


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

    @pytest.mark.parametrize(
        ("env_allowlist", "expected"),
        [
            pytest.param(
                None,
                {"GOPATH": "/work", "GORACE": "halt_on_error=1"},
                id="default",
            ),
            pytest.param(
                ["GOOGLE_CLOUD_PROJECT"],
                {
                    "GOPATH": "/work",
                    "GORACE": "halt_on_error=1",
                    "GOOGLE_CLOUD_PROJECT": "proj",
                },
                id="allowlisted",
            ),
        ],
    )
    def test_go_recording_persists_filtered_environment(
        self,
        tmp_path: Path,
        env_allowlist: list[str] | None,
        expected: dict[str, str],
    ) -> None:
        """The spy/passthrough workflow saves the filtered Go environment."""
        fixture_path = tmp_path / "go.json"
        with CmdMox() as mox:
            spy = (
                mox
                .spy("go")
                .passthrough()
                .record(fixture_path, env_allowlist=env_allowlist)
            )
            invocation = Invocation(
                command="go",
                args=["test", "-race", "./..."],
                stdin="",
                env={
                    "GOPATH": "/work",
                    "GORACE": "halt_on_error=1",
                    "GOOGLE_CLOUD_PROJECT": "proj",
                    "PATH": "/usr/bin",
                },
            )
            response = mox._prepare_passthrough(spy, invocation)
            directive = response.passthrough
            assert directive is not None

            mox._handle_passthrough_result(
                PassthroughResult(
                    invocation_id=directive.invocation_id,
                    stdout="ok\n",
                    stderr="",
                    exit_code=0,
                )
            )
            session = spy.recording_session
            assert session is not None
            session.finalize()

        fixture = FixtureFile.load(fixture_path)
        assert fixture.recordings[0].env_subset == expected

    def test_with_env_key_outside_known_prefix_is_recorded(
        self, tmp_path: Path
    ) -> None:
        """A ``.with_env()`` key is recorded even without the tool's prefix."""
        env_subset = _record_env_subset(
            tmp_path,
            command="git",
            with_env={"CUSTOM_KEY": "custom"},
            invocation_env={"CUSTOM_KEY": "custom", "GIT_DIR": ".git"},
        )

        assert env_subset == {"CUSTOM_KEY": "custom", "GIT_DIR": ".git"}

    def test_known_prefix_sensitive_with_env_key_is_recorded(
        self, tmp_path: Path
    ) -> None:
        """An explicitly requested sensitive key survives the sensitivity rule."""
        env_subset = _record_env_subset(
            tmp_path,
            command="git",
            with_env={"GIT_TOKEN": "s3cr3t"},
            invocation_env={"GIT_AUTHOR_NAME": "Ada"},
        )

        assert env_subset == {
            "GIT_TOKEN": "s3cr3t",
            "GIT_AUTHOR_NAME": "Ada",
        }

    def test_unknown_command_records_no_unrequested_keys(self, tmp_path: Path) -> None:
        """Passthrough recording uses the restrictive unknown-command default."""
        env_subset = _record_env_subset(
            tmp_path,
            command="greet",
            invocation_env={"HOSTISH_VALUE": "host", "MY_SETTING": "enabled"},
        )

        assert env_subset == {}

    def test_with_env_snapshot_survives_expectation_mutation(
        self, tmp_path: Path
    ) -> None:
        """Mutating the expectation after preparation does not change the record."""
        fixture_path = tmp_path / "fixture.json"
        with CmdMox() as mox:
            spy = (
                mox
                .spy("git")
                .with_env({"CUSTOM_KEY": "custom"})
                .passthrough()
                .record(fixture_path)
            )
            invocation = Invocation(command="git", args=[], stdin="", env={})
            response = mox._prepare_passthrough(spy, invocation)
            directive = response.passthrough
            assert directive is not None

            spy.expectation.env.clear()

            _complete_passthrough(mox, directive)

        assert spy._recording_session is not None
        assert spy._recording_session._recordings[0].env_subset == {
            "CUSTOM_KEY": "custom"
        }

    def test_two_requests_keep_their_own_with_env_keys(self, tmp_path: Path) -> None:
        """Requests completed in reverse order retain their own explicit keys."""
        fixture_path = tmp_path / "fixture.json"
        with CmdMox() as mox:
            spy = (
                mox
                .spy("git")
                .with_env({"FIRST_KEY": "1"})
                .passthrough()
                .record(fixture_path)
            )
            first = mox._prepare_passthrough(
                spy, Invocation(command="git", args=["first"], stdin="", env={})
            )
            first_directive = first.passthrough
            assert first_directive is not None

            spy.with_env({"SECOND_KEY": "2"})
            second = mox._prepare_passthrough(
                spy, Invocation(command="git", args=["second"], stdin="", env={})
            )
            second_directive = second.passthrough
            assert second_directive is not None

            # Complete the second request before the first.
            _complete_passthrough(mox, second_directive)
            _complete_passthrough(mox, first_directive)

        assert spy._recording_session is not None
        recordings = spy._recording_session._recordings
        assert recordings[0].args == ["second"]
        assert recordings[0].env_subset == {"SECOND_KEY": "2"}
        assert recordings[1].args == ["first"]
        assert recordings[1].env_subset == {"FIRST_KEY": "1"}
