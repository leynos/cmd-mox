"""Pytest plugin providing the ``cmd_mox`` fixture."""

from __future__ import annotations

import logging
import os
import typing as t

import pytest

from .controller import CmdMox
from .environment import EnvironmentManager
from .platform import skip_if_unsupported

logger = logging.getLogger(__name__)


@pytest.fixture
def cmd_mox(request: pytest.FixtureRequest) -> t.Generator[CmdMox, None, None]:
    """Provide a :class:`CmdMox` instance with environment active."""
    skip_if_unsupported()

    worker_id = os.getenv("PYTEST_XDIST_WORKER")
    if worker_id is None:
        worker_id = getattr(
            getattr(request.config, "workerinput", None), "workerid", "main"
        )
    prefix = f"cmdmox-{worker_id}-{os.getpid()}-"

    mox = CmdMox()
    mox.environment = EnvironmentManager(prefix=prefix)

    try:
        mox.__enter__()
        yield mox
    except Exception:
        logger.exception("Error during cmd_mox fixture setup or test execution")
        raise
    finally:
        try:
            mox.__exit__(None, None, None)
        except OSError:
            logger.exception("Error during cmd_mox fixture cleanup")
            # Re-raise cleanup errors to ensure test failure visibility
            pytest.fail("cmd_mox fixture cleanup failed")
