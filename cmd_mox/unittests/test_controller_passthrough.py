"""Unit tests for :mod:`cmd_mox.controller` - passthrough coordinator."""

from __future__ import annotations

import os

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.ipc import Invocation, PassthroughResult

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
        assert mox._passthrough_coordinator.has_pending(directive.invocation_id)


def test_prepare_passthrough_rejects_conflicting_env() -> None:
    """Passthrough invocations with conflicting env should raise."""
    key = "EXPECT_ENV"
    with CmdMox() as mox:
        spy = mox.spy("echo").with_env({key: "EXPECTED"}).passthrough()
        invocation = Invocation(command="echo", args=[], stdin="", env={key: "DIFF"})

        with pytest.raises(UnexpectedCommandError, match="conflicting environment"):
            mox._prepare_passthrough(spy, invocation)


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
        assert mox._passthrough_coordinator.has_pending(
            prepared.passthrough.invocation_id
        )


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
        assert not mox._passthrough_coordinator.has_pending(directive.invocation_id)
        with pytest.raises(RuntimeError, match="Unexpected passthrough result"):
            mox._handle_passthrough_result(result)
