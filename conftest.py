"""Global test configuration and shared fixtures."""

import typing as t

import pytest

import cmd_mox.environment


@pytest.fixture(autouse=True)
def reset_environment_manager_state() -> t.Generator[None, None, None]:
    """Ensure clean state for ``EnvironmentManager`` between tests."""
    cmd_mox.environment.EnvironmentManager.reset_active_manager()
    yield
    cmd_mox.environment.EnvironmentManager.reset_active_manager()
