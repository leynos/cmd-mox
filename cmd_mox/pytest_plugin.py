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
    if rep.when != "call":
        return
    _verify_during_call(item, rep)


def _verify_during_call(item: pytest.Item, rep: pytest.TestReport) -> None:
    """Run ``verify()`` for ``item`` when the call phase completes."""
    mox: CmdMox | None = getattr(item, "_cmd_mox_instance", None)
    if mox is None:
        return
    if not getattr(item, "_cmd_mox_auto_lifecycle", True):
        return
    if mox.phase is not Phase.REPLAY:
        return
    try:
        mox.verify()
    except Exception as err:
        logger.exception("Error during cmd_mox verification")
        if rep.failed:
            return
        _apply_verify_failure(item, rep, err)


def _auto_lifecycle_enabled(request: pytest.FixtureRequest) -> bool:
    """Return whether the fixture should manage replay/verify automatically."""
    marker_value = _marker_auto_lifecycle(request.node.get_closest_marker("cmd_mox"))
    if marker_value is not None:
        return marker_value

    param_value = _param_auto_lifecycle(getattr(request, "param", None))
    if param_value is not None:
        return param_value

    cli_value = request.config.getoption("cmd_mox_auto_lifecycle")
    if cli_value is not None:
        return bool(cli_value)

    return bool(request.config.getini("cmd_mox_auto_lifecycle"))


def _marker_auto_lifecycle(marker: pytest.Mark | None) -> bool | None:
    """Return the auto-lifecycle override declared via ``@pytest.mark.cmd_mox``."""
    if marker is None:
        return None
    if "auto_lifecycle" in marker.kwargs:
        return bool(marker.kwargs["auto_lifecycle"])
    return None


def _param_auto_lifecycle(param: object | None) -> bool | None:
    """Interpret parametrised fixture arguments for ``cmd_mox``."""
    if param is None:
        return None
    if isinstance(param, dict):
        mapping = t.cast("dict[str, object]", param)
        if "auto_lifecycle" in mapping:
            return bool(mapping["auto_lifecycle"])
        msg = "cmd_mox fixture param must be a bool or dict with 'auto_lifecycle' key"
        raise TypeError(msg)
    if isinstance(param, bool):
        return param
    msg = "cmd_mox fixture param must be a bool or dict with 'auto_lifecycle' key"
    raise TypeError(msg)


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

    mox = CmdMox(verify_on_exit=False)
    mox.environment = EnvironmentManager(prefix=_worker_prefix(request))

    auto_lifecycle = _auto_lifecycle_enabled(request)
    try:
        _enter_cmd_mox(mox, auto_lifecycle=auto_lifecycle)
        _attach_node_state(request.node, mox, auto_lifecycle=auto_lifecycle)
        yield mox
    except Exception:
        logger.exception("Error during cmd_mox fixture setup or test execution")
        raise
    finally:
        _teardown_cmd_mox(request.node, mox)


def _worker_prefix(request: pytest.FixtureRequest) -> str:
    """Build a stable prefix that keeps shims distinct per worker."""
    worker_id = os.getenv("PYTEST_XDIST_WORKER")
    if worker_id is None:
        worker_input = getattr(request.config, "workerinput", None)
        worker_id = getattr(worker_input, "workerid", "main")
    return f"cmdmox-{worker_id}-{os.getpid()}-"


def _enter_cmd_mox(mox: CmdMox, *, auto_lifecycle: bool) -> None:
    """Enter the controller context and optionally replay immediately."""
    mox.__enter__()
    if auto_lifecycle:
        mox.replay()


def _attach_node_state(item: pytest.Item, mox: CmdMox, *, auto_lifecycle: bool) -> None:
    """Expose ``mox`` on the test item for later teardown hooks."""
    item._cmd_mox_instance = mox  # type: ignore[attr-defined]
    item._cmd_mox_auto_lifecycle = auto_lifecycle  # type: ignore[attr-defined]


def _teardown_cmd_mox(item: pytest.Item, mox: CmdMox) -> None:
    """Exit the controller context and clear per-item state."""
    try:
        if mox.phase is not Phase.VERIFY:
            mox.__exit__(None, None, None)
    except OSError:
        logger.exception("Error during cmd_mox fixture cleanup")
        pytest.fail("cmd_mox fixture cleanup failed")
    finally:
        _detach_node_state(item, mox)


def _detach_node_state(item: pytest.Item, mox: CmdMox) -> None:
    """Remove per-item hooks referencing ``mox``."""
    if getattr(item, "_cmd_mox_instance", None) is mox:
        delattr(item, "_cmd_mox_instance")
    if hasattr(item, "_cmd_mox_auto_lifecycle"):
        delattr(item, "_cmd_mox_auto_lifecycle")
