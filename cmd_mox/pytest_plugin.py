"""Pytest plugin providing the ``cmd_mox`` fixture."""

from __future__ import annotations

import os
import typing as t

import pytest

from .controller import CmdMox
from .environment import EnvironmentManager


@pytest.fixture
def cmd_mox(request: pytest.FixtureRequest) -> t.Generator[CmdMox, None, None]:
    """Provide a :class:`CmdMox` instance with environment active."""
    worker_id = os.getenv("PYTEST_XDIST_WORKER")
    if worker_id is None:
        worker_id = getattr(
            getattr(request.config, "workerinput", None), "workerid", "main"
        )
    prefix = f"cmdmox-{worker_id}-{os.getpid()}-"

    mox = CmdMox()
    mox.environment = EnvironmentManager(prefix=prefix)
    mox.__enter__()
    try:
        yield mox
    finally:
        mox.__exit__(None, None, None)
