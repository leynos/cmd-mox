"""Unit tests for :mod:`cmd_mox.controller` - passthrough coordinator."""

from __future__ import annotations

import os
import typing as typ

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.ipc import Invocation, PassthroughResult

if typ.TYPE_CHECKING:
    from cmd_mox.test_doubles import CommandDouble

pytestmark = pytest.mark.requires_unix_sockets


def test_prepare_passthrough_registers_pending_invocation() -> None:
    """_prepare_passthrough stores directives for the shim."""
    with CmdMox() as mox:
        spy = mox.spy("echo").passthrough()
        invocation = Invocation(command="echo", args=["hi"], stdin="", env={})
        response = mox._prepare_passthrough(spy, invocation)

        assert response.passthrough is not None
        directive = response.passthrough
        assert invocation.invocation_id == directive.invocation_id
        assert directive.lookup_path == mox.environment.original_environment.get(
            "PATH", os.environ.get("PATH", "")
        )


def test_prepare_passthrough_rejects_conflicting_env() -> None:
    """Passthrough invocations with conflicting env should raise."""
    key = "EXPECT_ENV"
    with CmdMox() as mox:
        spy = mox.spy("echo").with_env({key: "EXPECTED"}).passthrough()
        invocation = Invocation(command="echo", args=[], stdin="", env={key: "DIFF"})

        with pytest.raises(UnexpectedCommandError, match="conflicting environment"):
            mox._prepare_passthrough(spy, invocation)


def test_prepare_passthrough_masks_conflicting_secret_env() -> None:
    """The passthrough path propagates the masked conflict message."""
    key = "GITHUB_KEY"
    provided_value = "invocation-secret"
    with CmdMox() as mox:
        spy = mox.spy("echo").with_env({key: "expected-secret"}).passthrough()
        invocation = Invocation(
            command="echo", args=[], stdin="", env={key: provided_value}
        )

        with pytest.raises(
            UnexpectedCommandError, match="conflicting environment"
        ) as exc:
            mox._prepare_passthrough(spy, invocation)

        message = str(exc.value)
        assert f"{key}='***'" in message, "Sensitive conflict was not masked"
        assert provided_value not in message, "Invocation value leaked into the message"


def test_zero_count_passthrough_spy_counts_in_flight_invocation() -> None:
    """An in-flight passthrough call violates an explicit zero count."""
    with CmdMox() as mox:
        spy = mox.spy("echo").times(0).passthrough()
        mox.replay()
        invocation = Invocation(command="echo", args=["hi"], stdin="", env={})
        mox._prepare_passthrough(spy, invocation)

        with pytest.raises(
            UnexpectedCommandError, match="Unexpected additional invocation"
        ):
            mox.verify()


def test_explicit_count_is_satisfied_by_in_flight_passthrough_call() -> None:
    """An in-flight passthrough call counts towards an explicit count."""
    with CmdMox() as mox:
        spy = mox.spy("echo").times(1).passthrough()
        mox.replay()
        invocation = Invocation(command="echo", args=["hi"], stdin="", env={})
        mox._prepare_passthrough(spy, invocation)

        mox.verify()


def test_completed_passthrough_call_counts_once() -> None:
    """A call recorded before verification is not counted twice."""
    with CmdMox() as mox:
        spy = mox.spy("echo").times(1).passthrough()
        mox.replay()
        invocation = Invocation(command="echo", args=["hi"], stdin="", env={})
        response = mox._prepare_passthrough(spy, invocation)
        directive = response.passthrough
        assert directive is not None
        mox._handle_passthrough_result(
            PassthroughResult(
                invocation_id=directive.invocation_id,
                stdout="out",
                stderr="",
                exit_code=0,
            )
        )

        mox.verify()


def test_completion_during_verification_counts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A call completing between the snapshots is counted exactly once."""
    with CmdMox() as mox:
        spy = mox.spy("echo").times(1).passthrough()
        mox.replay()
        invocation = Invocation(command="echo", args=["hi"], stdin="", env={})
        response = mox._prepare_passthrough(spy, invocation)
        directive = response.passthrough
        assert directive is not None
        coordinator = mox._passthrough_coordinator
        snapshot = coordinator.unrecorded_invocations
        completed = False

        def snapshot_then_complete() -> list[tuple[CommandDouble, Invocation]]:
            """Snapshot the registry, then finalise the call before returning."""
            nonlocal completed
            entries = snapshot()
            if not completed:
                completed = True
                mox._handle_passthrough_result(
                    PassthroughResult(
                        invocation_id=directive.invocation_id,
                        stdout="out",
                        stderr="",
                        exit_code=0,
                    )
                )
            return entries

        monkeypatch.setattr(
            coordinator, "unrecorded_invocations", snapshot_then_complete
        )

        mox.verify()

        assert completed, "the interleaving did not run"


def test_expired_passthrough_call_still_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A passthrough call that expired before verification still counts."""

    class _Clock:
        """Frozen monotonic clock for the passthrough coordinator."""

        now = 100.0

        @classmethod
        def monotonic(cls) -> float:
            """Return the frozen time."""
            return cls.now

    monkeypatch.setattr("cmd_mox.passthrough.time", _Clock)
    with CmdMox() as mox:
        spy = mox.spy("echo").times(1).passthrough()
        mox.replay()
        invocation = Invocation(command="echo", args=["hi"], stdin="", env={})
        mox._prepare_passthrough(spy, invocation)
        _Clock.now += 10_000.0

        mox.verify()


def test_handle_passthrough_result_rejects_unknown_invocation() -> None:
    """Unexpected passthrough results should raise a clear RuntimeError."""
    with CmdMox() as mox:
        result = PassthroughResult(
            invocation_id="missing",
            stdout="",
            stderr="",
            exit_code=0,
        )
        with pytest.raises(RuntimeError, match="Unexpected passthrough result"):
            mox._handle_passthrough_result(result)

        spy = mox.spy("echo").passthrough()
        invocation = Invocation(command="echo", args=["hi"], stdin="", env={})
        prepared = mox._prepare_passthrough(spy, invocation)
        assert prepared.passthrough is not None


def test_handle_passthrough_result_finalises_invocation() -> None:
    """_handle_passthrough_result records journal entries and clears state."""
    with CmdMox() as mox:
        spy = mox.spy("echo").passthrough()
        invocation = Invocation(command="echo", args=["hello"], stdin="", env={})
        response = mox._prepare_passthrough(spy, invocation)
        directive = response.passthrough
        assert directive is not None

        result = PassthroughResult(
            invocation_id=directive.invocation_id,
            stdout="out",
            stderr="",
            exit_code=7,
        )
        final = mox._handle_passthrough_result(result)

        assert final.stdout == "out"
        assert spy.invocations[0].stdout == "out"
        assert len(mox.journal) == 1
        recorded = mox.journal[0]
        assert recorded.exit_code == 7
        with pytest.raises(RuntimeError, match="Unexpected passthrough result"):
            mox._handle_passthrough_result(result)
