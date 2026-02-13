"""pytest-bdd steps focused on controller lifecycle orchestration."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import pytest
from pytest_bdd import given, parsers, then, when

from cmd_mox.controller import CmdMox
from cmd_mox.environment import EnvironmentManager
from cmd_mox.errors import (
    MissingEnvironmentError,
    UnexpectedCommandError,
    UnfulfilledExpectationError,
    VerificationError,
)
from cmd_mox.ipc import CallbackNamedPipeServer

_ERROR_TYPES: dict[str, type[VerificationError]] = {
    "UnexpectedCommandError": UnexpectedCommandError,
    "UnfulfilledExpectationError": UnfulfilledExpectationError,
    "VerificationError": VerificationError,
}


@given("a CmdMox controller", target_fixture="mox")
def create_controller() -> CmdMox:
    """Create a fresh CmdMox instance."""
    return CmdMox()


@given(
    parsers.cfparse("a CmdMox controller with max journal size {size:d}"),
    target_fixture="mox",
)
def create_controller_with_limit(size: int) -> CmdMox:
    """Create a CmdMox instance with bounded journal."""
    return CmdMox(max_journal_entries=size)


@given(
    parsers.cfparse("creating a CmdMox controller with max journal size {size:d} fails")
)
def create_controller_with_limit_fails(size: int) -> None:
    """Assert constructing a controller with invalid journal size fails."""
    with pytest.raises(ValueError, match="max_journal_entries must be positive"):
        CmdMox(max_journal_entries=size)


@given("replay startup is interrupted by KeyboardInterrupt")
def interrupt_replay_startup(monkeypatch: pytest.MonkeyPatch, mox: CmdMox) -> None:
    """Simulate Ctrl+C during replay startup by raising ``KeyboardInterrupt``."""

    def raise_interrupt() -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(mox, "_start_ipc_server", raise_interrupt)


@given("the replay environment is invalidated during startup")
def invalidate_environment(monkeypatch: pytest.MonkeyPatch, mox: CmdMox) -> None:
    """Cause environment attributes to disappear after preflight checks."""
    original = mox._check_replay_preconditions

    def tampered() -> None:
        original()
        env = mox.environment
        assert env is not None, "Environment manager should exist"
        env.shim_dir = None
        env.socket_path = None

    monkeypatch.setattr(mox, "_check_replay_preconditions", tampered)


@when("I replay the controller", target_fixture="mox_stack")
def replay_controller(mox: CmdMox) -> contextlib.ExitStack:
    """Enter replay mode within a context manager."""
    stack = contextlib.ExitStack()
    stack.enter_context(mox)
    mox.replay()
    return stack


@when(
    "I replay the controller expecting a missing environment error",
    target_fixture="replay_error",
)
def replay_controller_missing_env(mox: CmdMox) -> MissingEnvironmentError:
    """Attempt replay expecting :class:`MissingEnvironmentError`."""
    with contextlib.ExitStack() as stack:
        stack.enter_context(mox)
        with pytest.raises(MissingEnvironmentError) as excinfo:
            mox.replay()
    return excinfo.value


@when(
    "I replay the controller expecting an interrupt",
    target_fixture="replay_interruption_state",
)
def replay_controller_interrupt(mox: CmdMox) -> dict[str, object]:
    """Run replay() and capture cleanup details when startup aborts."""
    env = mox.environment
    assert env is not None, "Replay environment was not initialised"

    mox.__enter__()
    assert env.shim_dir is not None, "Replay environment was not initialised"
    assert env.socket_path is not None, "Replay environment was not initialised"
    shim_dir = Path(env.shim_dir)
    socket_path = Path(env.socket_path)
    assert shim_dir.exists()

    with pytest.raises(KeyboardInterrupt):
        mox.replay()

    return {
        "shim_dir": shim_dir,
        "socket_path": socket_path,
        "manager_active": EnvironmentManager.get_active_manager(),
    }


@when("I verify the controller")
def verify_controller(mox: CmdMox, mox_stack: contextlib.ExitStack) -> None:
    """Invoke verification and close context."""
    try:
        mox.verify()
    finally:
        mox_stack.close()


@when(
    parsers.cfparse("I verify the controller expecting an {error_name}"),
    target_fixture="verification_error",
)
def verify_controller_expect_error(
    mox: CmdMox, mox_stack: contextlib.ExitStack, error_name: str
) -> VerificationError:
    """Invoke verification expecting a specific error type."""
    error_type = _ERROR_TYPES.get(error_name)
    if error_type is None:  # pragma: no cover - invalid feature configuration
        msg = f"Unknown verification error type: {error_name}"
        raise ValueError(msg)
    try:
        with pytest.raises(error_type) as excinfo:
            mox.verify()
    finally:
        mox_stack.close()
    return excinfo.value


@then("the controller should use the Windows named pipe server")
def assert_windows_named_pipe_server(mox: CmdMox) -> None:
    """Assert the controller swaps to the named pipe transport on Windows."""
    if os.name != "nt":  # pragma: no cover - guarded by feature preconditions
        pytest.skip("Named pipe assertions only apply on Windows")
    server = mox._server
    assert isinstance(server, CallbackNamedPipeServer), server
