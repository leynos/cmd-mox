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


class _CmdMoxItem(t.Protocol):
    """pytest item carrying cmd_mox lifecycle metadata."""

    _cmd_mox_instance: CmdMox | None
    _cmd_mox_auto_lifecycle: bool
    _cmd_mox_verify_error: Exception | None
    _cmd_mox_verify_should_fail: bool


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[t.Any]
) -> t.Generator[None, None, None]:
    """Attach the call/report objects to each collected test item.

    This enables later hooks to inspect outcomes and trigger cmd_mox
    verification based on the test's success or failure.
    """
    del call
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)
    if rep.when == "teardown":
        _apply_deferred_verify_failure(item, rep)


def _auto_lifecycle_enabled(request: pytest.FixtureRequest) -> bool:
    """Return whether the fixture should manage replay/verify automatically."""
    # Priority order: marker > fixture param > CLI option > INI setting

    marker_value = _get_marker_auto_lifecycle(request)
    if marker_value is not None:
        return marker_value

    param_value = _get_param_auto_lifecycle(request)
    if param_value is not None:
        return param_value

    config = request.config
    cli_value = config.getoption("cmd_mox_auto_lifecycle")
    if cli_value is not None:
        return bool(cli_value)

    return bool(config.getini("cmd_mox_auto_lifecycle"))


def _get_marker_auto_lifecycle(request: pytest.FixtureRequest) -> bool | None:
    """Return marker override for auto lifecycle if present."""
    marker = request.node.get_closest_marker("cmd_mox")
    if marker is None or "auto_lifecycle" not in marker.kwargs:
        return None
    return bool(marker.kwargs["auto_lifecycle"])


def _get_param_auto_lifecycle(request: pytest.FixtureRequest) -> bool | None:
    """Return fixture parameter override for auto lifecycle if present."""
    param = getattr(request, "param", None)
    if param is None:
        return None
    if isinstance(param, dict):
        if "auto_lifecycle" in param:
            return bool(param["auto_lifecycle"])
        keys = list(param.keys())
        msg = (
            "cmd_mox fixture param dict must contain 'auto_lifecycle' key, "
            f"got keys: {keys}"
        )
        raise TypeError(msg)
    if isinstance(param, bool):
        return param
    msg = (
        "cmd_mox fixture param must be a bool or dict with 'auto_lifecycle' key, "
        f"got {type(param).__name__}"
    )
    raise TypeError(msg)


def _apply_verify_failure(
    item: pytest.Item, report: pytest.TestReport, err: Exception
) -> None:
    """Record *err* as a failure on ``report``."""
    report.outcome = "failed"
    report.longrepr = f"{type(err).__name__}: {err}"


def _apply_deferred_verify_failure(
    item: pytest.Item, report: pytest.TestReport
) -> None:
    """Apply any deferred verification error captured during teardown."""
    err: Exception | None = getattr(item, "_cmd_mox_verify_error", None)
    if err is None:
        return
    delattr(item, "_cmd_mox_verify_error")
    should_fail = getattr(item, "_cmd_mox_verify_should_fail", False)
    if hasattr(item, "_cmd_mox_verify_should_fail"):
        delattr(item, "_cmd_mox_verify_should_fail")
    if not should_fail:
        report.sections.append(("cmd_mox verification", f"{type(err).__name__}: {err}"))


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
        if isinstance(worker_input, dict):
            mapping_worker_id = worker_input.get("workerid")
            worker_id = "main" if mapping_worker_id is None else str(mapping_worker_id)
        else:
            attribute_worker_id = getattr(worker_input, "workerid", None)
            worker_id = (
                "main" if attribute_worker_id is None else str(attribute_worker_id)
            )
    return f"cmdmox-{worker_id}-{os.getpid()}-"


def _enter_cmd_mox(mox: CmdMox, *, auto_lifecycle: bool) -> None:
    """Enter the controller context and optionally replay immediately."""
    mox.__enter__()
    if auto_lifecycle:
        mox.replay()


def _attach_node_state(item: pytest.Item, mox: CmdMox, *, auto_lifecycle: bool) -> None:
    """Expose ``mox`` on the test item for later teardown hooks."""
    typed_item = t.cast("_CmdMoxItem", item)
    typed_item._cmd_mox_instance = mox
    typed_item._cmd_mox_auto_lifecycle = auto_lifecycle
    typed_item._cmd_mox_verify_error = None
    typed_item._cmd_mox_verify_should_fail = False


def _teardown_cmd_mox(item: pytest.Item, mox: CmdMox) -> None:
    """Exit the controller context and clear per-item state."""
    typed_item = t.cast("_CmdMoxItem", item)
    auto_lifecycle = getattr(typed_item, "_cmd_mox_auto_lifecycle", True)
    should_verify = auto_lifecycle and mox.phase is Phase.REPLAY
    should_raise = False
    if should_verify:
        try:
            mox.verify()
        except Exception as err:
            logger.exception("Error during cmd_mox verification")
            typed_item._cmd_mox_verify_error = err
            should_fail = not _call_stage_failed(item)
            typed_item._cmd_mox_verify_should_fail = should_fail
            should_raise = should_fail
    try:
        mox.__exit__(None, None, None)
    except Exception:
        logger.exception("Error during cmd_mox fixture cleanup")
        pytest.fail("cmd_mox fixture cleanup failed")
    finally:
        _detach_node_state(item, mox)
    if should_raise:
        err = typed_item._cmd_mox_verify_error
        pytest.fail(f"{type(err).__name__}: {err}")


def _detach_node_state(item: pytest.Item, mox: CmdMox) -> None:
    """Remove per-item hooks referencing ``mox``."""
    typed_item = t.cast("_CmdMoxItem", item)
    if getattr(typed_item, "_cmd_mox_instance", None) is mox:
        delattr(typed_item, "_cmd_mox_instance")
    if hasattr(typed_item, "_cmd_mox_auto_lifecycle"):
        delattr(typed_item, "_cmd_mox_auto_lifecycle")


def _call_stage_failed(item: pytest.Item) -> bool:
    """Return ``True`` when the test body has already failed."""
    rep_call = getattr(item, "rep_call", None)
    return bool(rep_call and rep_call.failed)
