"""Ensure stub responses do not retain injected environment variables."""

from __future__ import annotations

from cmd_mox.controller import CmdMox
from tests.helpers.controller import CommandExecution, _execute_command_with_params


def test_stub_response_env_is_isolated() -> None:
    """Different expectation envs should not leak into static responses."""
    with CmdMox() as mox:
        stub = mox.stub("foo").with_env({"A": "1"}).returns(stdout="ok").times(2)
        mox.replay()

        params = CommandExecution(
            cmd="foo", args="", stdin="", env_var="A", env_val="1"
        )
        _execute_command_with_params(params)
        assert stub.response.env == {}

        stub.expectation.with_env({"B": "2"})
        params = CommandExecution(
            cmd="foo", args="", stdin="", env_var="B", env_val="2"
        )
        _execute_command_with_params(params)
        assert stub.response.env == {}

        # Relax expectation so verification does not fail on env mismatch.
        stub.expectation.with_env({})
