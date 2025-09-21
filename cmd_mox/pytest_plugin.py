"""Pytest plugin providing the ``cmd_mox`` fixture."""

from __future__ import annotations

import logging
import os
import typing as t

import pytest

from .controller import CmdMox, Phase
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

    mox = CmdMox(verify_on_exit=False)
    mox.environment = EnvironmentManager(prefix=prefix)

    try:
        mox.__enter__()
        mox.replay()
        request.node._cmd_mox_instance = mox  # type: ignore[attr-defined]
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
        if getattr(request.node, "_cmd_mox_instance", None) is mox:
            delattr(request.node, "_cmd_mox_instance")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item: pytest.Item) -> t.Generator[None, None, None]:
    """Automatically verify :mod:`cmd_mox` expectations after each test."""
    mox: CmdMox | None = getattr(item, "_cmd_mox_instance", None)
    outcome = yield
    if mox is None:
        return

    verify_error: Exception | None = None
    should_verify = getattr(mox, "phase", None) is Phase.REPLAY
    if should_verify:
        try:
            mox.verify()
        except Exception as err:
            verify_error = err
            logger.exception("Error during cmd_mox verification")
    if verify_error is not None and outcome.excinfo is None:
        raise verify_error.with_traceback(verify_error.__traceback__)
