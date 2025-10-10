"""Ensure stub responses do not retain injected environment variables."""

from __future__ import annotations

import pytest

from cmd_mox.controller import CmdMox
from tests.helpers.controller import CommandExecution, _execute_command_with_params

pytestmark = pytest.mark.requires_unix_sockets


def test_stub_response_env_is_isolated() -> None:
    """Different expectation envs should not leak into static responses."""
    with CmdMox() as mox:
        # Use distinct one-shot stubs to avoid mutating expectations mid-test.
        stub = mox.stub("foo").with_env({"A": "1"}).returns(stdout="ok").times(1)
        mox.replay()

        params = CommandExecution(
            cmd="foo", args="", stdin="", env_var="A", env_val="1"
        )
        result = _execute_command_with_params(params)
        assert result.stdout == "ok"
        assert result.returncode == 0
        assert stub.response.env == {}

        stub2 = mox.stub("foo").with_env({"B": "2"}).returns(stdout="ok").times(1)
        params = CommandExecution(
            cmd="foo", args="", stdin="", env_var="B", env_val="2"
        )
        result = _execute_command_with_params(params)
        assert result.stdout == "ok"
        assert result.returncode == 0
        assert stub.response.env == {}
        assert stub2.response.env == {}
