"""Pytest plugin providing the ``cmd_mox`` fixture."""

from __future__ import annotations

import logging
import os
import re
import typing as t

import pytest

from .controller import CmdMox, Phase
from .environment import EnvironmentManager
from .platform import skip_if_unsupported

logger = logging.getLogger(__name__)

# Stash key to record per-item call-stage failure state.
STASH_CALL_FAILED: pytest.StashKey[bool] = pytest.StashKey()


def _build_worker_prefix(config: pytest.Config) -> str:
    """Return an EnvironmentManager prefix scoped to the current worker."""
    worker_id = os.getenv("PYTEST_XDIST_WORKER")
    if worker_id is None:
        worker_input = getattr(config, "workerinput", None)
        wid = None
        if isinstance(worker_input, dict):
            wid = worker_input.get("workerid")
        else:
            wid = getattr(worker_input, "workerid", None)
        worker_id = "main" if wid is None else str(wid)
    safe = _sanitize_worker_id(str(worker_id))
    return f"cmdmox-{safe}-{os.getpid()}-"


def _sanitize_worker_id(value: str) -> str:
    """Collapse worker identifiers to filesystem-safe characters."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value)


def _aggregate_teardown_errors(
    verify_error: Exception | None, exit_error: Exception | None
) -> list[tuple[str, Exception]]:
    """Collect teardown errors for aggregation and reporting."""
    errors: list[tuple[str, Exception]] = []
    if verify_error is not None:
        errors.append(("verification", verify_error))
    if exit_error is not None:
        errors.append(("cleanup", exit_error))
    return errors


def _format_teardown_failure(errors: list[tuple[str, Exception]]) -> str:
    """Format an aggregated error message for pytest failures."""
    if not errors:
        return "cmd_mox teardown failure"
    if len(errors) == 1:
        stage, err = errors[0]
        return f"cmd_mox {stage} {type(err).__name__}: {err}"
    joined = "; ".join(f"{stage} {type(err).__name__}: {err}" for stage, err in errors)
    return f"cmd_mox teardown failure: {joined}"


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
    """Record whether the test body failed for later teardown decisions."""
    del call
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call":
        item.stash[STASH_CALL_FAILED] = rep.failed


class _CmdMoxManager:
    """Encapsulate cmd_mox lifecycle decisions for the pytest fixture."""

    def __init__(self, request: pytest.FixtureRequest) -> None:
        self.request = request
        self.config = request.config
        self._auto_lifecycle = self._auto_lifecycle_enabled()
        self.mox = CmdMox(
            verify_on_exit=False,
            environment=EnvironmentManager(prefix=_build_worker_prefix(self.config)),
        )
        # CmdMox wires its command runner to the provided environment during
        # initialisation, so injecting the worker-scoped manager here ensures the
        # replay lifecycle observes the correct PATH mutations once entered.
        self._entered = False

    @property
    def auto_lifecycle(self) -> bool:
        """Return whether replay/verify should be driven automatically."""
        return self._auto_lifecycle

    @property
    def entered(self) -> bool:
        """Return ``True`` once :meth:`enter` has successfully entered."""
        return self._entered

    def enter(self) -> None:
        """Enter the controller context and replay if configured."""
        self.mox.__enter__()
        self._entered = True
        if self._auto_lifecycle:
            self.mox.replay()

    def exit(self, *, body_failed: bool) -> None:
        """Verify (when appropriate) and tear down the controller."""
        if not self._entered:
            return

        effective_body_failed = self._determine_effective_failure(
            body_failed=body_failed
        )

        verify_error, exit_error = self._run_teardown_operations()
        self._entered = False

        if verify_error is None and exit_error is None:
            return

        if self._should_suppress_errors(
            verify_error, exit_error, effective_body_failed=effective_body_failed
        ):
            return

        self._handle_teardown_errors(verify_error, exit_error)

    def _determine_effective_failure(self, *, body_failed: bool) -> bool:
        """Return whether the body or call stage reported a failure."""
        call_failed = bool(self.request.node.stash.get(STASH_CALL_FAILED, False))
        if STASH_CALL_FAILED in self.request.node.stash:
            del self.request.node.stash[STASH_CALL_FAILED]
        return body_failed or call_failed

    def _run_teardown_operations(self) -> tuple[Exception | None, Exception | None]:
        """Execute verification and cleanup, returning any captured errors."""
        verify_error = self._verify_if_needed()
        exit_error = self._close_controller()
        return verify_error, exit_error

    def _should_suppress_errors(
        self,
        verify_error: Exception | None,
        exit_error: Exception | None,
        *,
        effective_body_failed: bool,
    ) -> bool:
        """Return True when teardown errors should be suppressed."""
        return verify_error is not None and exit_error is None and effective_body_failed

    def _handle_teardown_errors(
        self, verify_error: Exception | None, exit_error: Exception | None
    ) -> None:
        """Aggregate teardown errors and fail the test."""
        errors = _aggregate_teardown_errors(verify_error, exit_error)
        add_section = getattr(self.request.node, "add_report_section", None)
        if callable(add_section):
            for stage, err in errors:
                if stage == "verification":
                    # Verification failures already register a section when they
                    # occur so suppressed errors remain visible without
                    # duplicating entries here.
                    continue
                add_section(
                    "teardown",
                    f"cmd_mox {stage}",
                    f"{type(err).__name__}: {err}",
                )
        message = _format_teardown_failure(errors)
        pytest.fail(message)

    def _auto_lifecycle_enabled(self) -> bool:
        """Resolve the auto-lifecycle flag respecting all configuration sources.

        The fixture parameter provides the most granular override, followed by
        a ``@pytest.mark.cmd_mox`` marker, the command-line option, and finally
        the ``pytest.ini`` default.
        """
        param_value = self._param_override()
        if param_value is not None:
            return param_value

        marker_value = self._marker_override()
        if marker_value is not None:
            return marker_value

        cli_value = self.config.getoption("cmd_mox_auto_lifecycle")
        if cli_value is not None:
            return bool(cli_value)

        return bool(self.config.getini("cmd_mox_auto_lifecycle"))

    def _marker_override(self) -> bool | None:
        """Return marker override for auto lifecycle if configured."""
        marker = self.request.node.get_closest_marker("cmd_mox")
        if marker is None:
            return None
        if "auto_lifecycle" in marker.kwargs:
            return bool(marker.kwargs["auto_lifecycle"])
        return None

    def _param_override(self) -> bool | None:
        """Return fixture parameter override for auto lifecycle if present."""
        param = getattr(self.request, "param", None)
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

    def _verify_if_needed(self) -> Exception | None:
        """Run :meth:`CmdMox.verify` when auto lifecycle is active."""
        if not self._auto_lifecycle or self.mox.phase is not Phase.REPLAY:
            return None
        try:
            self.mox.verify()
        except Exception as err:
            logger.exception("Error during cmd_mox verification")
            self._add_verification_section(err)
            return err
        return None

    def _close_controller(self) -> Exception | None:
        """Invoke :meth:`CmdMox.__exit__` and capture cleanup failures."""
        try:
            self.mox.__exit__(None, None, None)
        except Exception as err:
            logger.exception("Error during cmd_mox fixture cleanup")
            return err
        return None

    def _add_verification_section(self, err: Exception) -> None:
        """Record verification errors as a teardown report section."""
        add_section = getattr(self.request.node, "add_report_section", None)
        if callable(add_section):
            add_section(
                "teardown",
                "cmd_mox verification",
                f"{type(err).__name__}: {err}",
            )


@pytest.fixture
def cmd_mox(request: pytest.FixtureRequest) -> t.Generator[CmdMox, None, None]:
    """Provide a :class:`CmdMox` instance with environment active."""
    skip_if_unsupported()

    manager = _CmdMoxManager(request)
    body_failed = False
    try:
        try:
            manager.enter()
        except Exception:
            body_failed = True
            logger.exception("Error during cmd_mox fixture setup or test execution")
            raise

        try:
            yield manager.mox
        except Exception:
            body_failed = True
            logger.debug(
                "Error during cmd_mox fixture setup or test execution",
                exc_info=True,
            )
            raise
    finally:
        manager.exit(body_failed=body_failed)
