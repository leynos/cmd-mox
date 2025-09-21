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


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register command-line and ini options for the plugin."""
    group = parser.getgroup("cmd_mox")
    group.addoption(
        "--cmd-mox-auto-lifecycle",
        action="store_true",
        dest="cmd_mox_auto_lifecycle",
        default=None,
        help=(
            "Enable automatic replay() before yielding the cmd_mox fixture and "
            "verify() during teardown. Overrides the pytest.ini setting."
        ),
    )
    group.addoption(
        "--no-cmd-mox-auto-lifecycle",
        action="store_false",
        dest="cmd_mox_auto_lifecycle",
        default=None,
        help=(
            "Disable automatic replay()/verify() around the cmd_mox fixture. "
            "Overrides the pytest.ini setting."
        ),
    )
    parser.addini(
        "cmd_mox_auto_lifecycle",
        (
            "Automatically call replay() before yielding the cmd_mox fixture "
            "and verify() during teardown."
        ),
        type="bool",
        default=True,
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register plugin-specific markers."""
    config.addinivalue_line(
        "markers",
        (
            "cmd_mox(auto_lifecycle: bool = True): override automatic "
            "replay()/verify() behaviour for a single test."
        ),
    )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[t.Any]
) -> t.Generator[None, None, None]:
    """Attach the call/report objects to each collected test item."""
    del call
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
    if rep.when == "call":
        mox: CmdMox | None = getattr(item, "_cmd_mox_instance", None)
        auto_lifecycle = getattr(item, "_cmd_mox_auto_lifecycle", True)
        if mox is None:
            return
        if auto_lifecycle and mox.phase is Phase.REPLAY:
            verify_error: Exception | None = None
            try:
                mox.verify()
            except Exception as err:
                verify_error = err
                logger.exception("Error during cmd_mox verification")
            if verify_error is not None and not rep.failed:
                _apply_verify_failure(item, rep, verify_error)


def _auto_lifecycle_enabled(request: pytest.FixtureRequest) -> bool:
    """Return whether the fixture should manage replay/verify automatically."""
    config = request.config
    auto_lifecycle: bool | None = None

    marker = request.node.get_closest_marker("cmd_mox")
    if marker is not None and "auto_lifecycle" in marker.kwargs:
        auto_lifecycle = bool(marker.kwargs["auto_lifecycle"])

    param = getattr(request, "param", None)
    if auto_lifecycle is None and param is not None:
        if isinstance(param, dict) and "auto_lifecycle" in param:
            auto_lifecycle = bool(param["auto_lifecycle"])
        elif isinstance(param, bool):
            auto_lifecycle = param
        else:  # pragma: no cover - defensive validation
            msg = (
                "cmd_mox fixture param must be a bool or dict with 'auto_lifecycle' key"
            )
            raise TypeError(msg)

    if auto_lifecycle is None:
        cli_value = config.getoption("cmd_mox_auto_lifecycle")
        if cli_value is not None:
            auto_lifecycle = bool(cli_value)

    if auto_lifecycle is None:
        auto_lifecycle = bool(config.getini("cmd_mox_auto_lifecycle"))

    return auto_lifecycle


def _apply_verify_failure(
    item: pytest.Item, report: pytest.TestReport, err: Exception
) -> None:
    """Record *err* as a failure on ``report``."""
    report.outcome = "failed"
    report.longrepr = f"{type(err).__name__}: {err}"


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

    auto_lifecycle = _auto_lifecycle_enabled(request)
    try:
        mox.__enter__()
        if auto_lifecycle:
            mox.replay()
        request.node._cmd_mox_instance = mox  # type: ignore[attr-defined]
        request.node._cmd_mox_auto_lifecycle = auto_lifecycle  # type: ignore[attr-defined]
        yield mox
    except Exception:
        logger.exception("Error during cmd_mox fixture setup or test execution")
        raise
    finally:
        exit_needed = mox.phase is not Phase.VERIFY
        try:
            if exit_needed:
                mox.__exit__(None, None, None)
        except OSError:
            logger.exception("Error during cmd_mox fixture cleanup")
            # Re-raise cleanup errors to ensure test failure visibility
            pytest.fail("cmd_mox fixture cleanup failed")
        if getattr(request.node, "_cmd_mox_instance", None) is mox:
            delattr(request.node, "_cmd_mox_instance")
        if hasattr(request.node, "_cmd_mox_auto_lifecycle"):
            delattr(request.node, "_cmd_mox_auto_lifecycle")
