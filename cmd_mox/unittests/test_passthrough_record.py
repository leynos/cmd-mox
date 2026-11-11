"""Unit tests for recording integration in PassthroughCoordinator."""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation, PassthroughResult
from cmd_mox.passthrough import PassthroughCoordinator
from cmd_mox.record.fixture import FixtureFile

if typ.TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.requires_unix_sockets


class _FakeClock:
    """Return scripted clock readings in call order."""

    def __init__(self, readings: list[float]) -> None:
        """Store the readings the coordinator should observe."""
        self._readings = iter(readings)

    def __call__(self) -> float:
        """Return the next scripted reading."""
        return next(self._readings)


def _record_one_invocation(tmp_path: Path, readings: list[float]) -> int:
    """Record a single invocation and return its measured duration."""
    with CmdMox() as mox:
        mox._passthrough_coordinator = PassthroughCoordinator(
            duration_clock=_FakeClock(readings)
        )
        spy = mox.spy("echo").passthrough().record(tmp_path / "fixture.json")
        invocation = Invocation(command="echo", args=["hello"], stdin="", env={})
        response = mox._prepare_passthrough(spy, invocation)
        directive = response.passthrough
        assert directive is not None
        mox._handle_passthrough_result(
            PassthroughResult(
                invocation_id=directive.invocation_id,
                stdout="hello\n",
                stderr="",
                exit_code=0,
            )
        )
        session = spy.recording_session
        assert session is not None
        return session._recordings[0].duration_ms


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

    def test_recorded_duration_is_measured_in_milliseconds(
        self, tmp_path: Path
    ) -> None:
        """A quarter-second clock interval is recorded as 250 ms."""
        assert _record_one_invocation(tmp_path, [10.0, 10.25]) == 250

    def test_out_of_order_results_keep_independent_durations(
        self, tmp_path: Path
    ) -> None:
        """Concurrent requests use their own start readings, not arrival order."""
        clock = _FakeClock([1.0, 2.0, 2.5, 4.0])
        with CmdMox() as mox:
            mox._passthrough_coordinator = PassthroughCoordinator(duration_clock=clock)
            spy = mox.spy("echo").passthrough().record(tmp_path / "fixture.json")
            first = mox._prepare_passthrough(
                spy, Invocation(command="echo", args=["first"], stdin="", env={})
            )
            second = mox._prepare_passthrough(
                spy, Invocation(command="echo", args=["second"], stdin="", env={})
            )
            first_directive = first.passthrough
            second_directive = second.passthrough
            assert first_directive is not None
            assert second_directive is not None

            # Finalise the second request before the first to prove the
            # duration follows the invocation ID rather than insertion order.
            for directive, stdout in (
                (second_directive, "second\n"),
                (first_directive, "first\n"),
            ):
                mox._handle_passthrough_result(
                    PassthroughResult(
                        invocation_id=directive.invocation_id,
                        stdout=stdout,
                        stderr="",
                        exit_code=0,
                    )
                )

            session = spy.recording_session
            assert session is not None
            durations = {
                recording.args[0]: recording.duration_ms
                for recording in session._recordings
            }

        assert durations == {"first": 3000, "second": 500}

    def test_sub_millisecond_interval_rounds_to_zero(self, tmp_path: Path) -> None:
        """An interval below half a millisecond rounds down to 0 ms."""
        assert _record_one_invocation(tmp_path, [10.0, 10.0004]) == 0

    def test_backwards_clock_clamps_to_zero(self, tmp_path: Path) -> None:
        """A clock that moves backwards records 0 ms instead of raising."""
        assert _record_one_invocation(tmp_path, [10.0, 9.5]) == 0

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
