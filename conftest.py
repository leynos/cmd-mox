"""Global test configuration and shared fixtures."""

import typing as t

import pytest

import cmd_mox.environment


@pytest.fixture(autouse=True)
def reset_environment_manager_state() -> t.Generator[None, None, None]:
    """Ensure clean global state for EnvironmentManager between tests."""
    # Reset class state before test
    cmd_mox.environment.EnvironmentManager._active_manager = None
    yield
    # Reset class state after test
    cmd_mox.environment.EnvironmentManager._active_manager = None
