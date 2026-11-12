"""Unit tests for the internal cmd_mox pytest plugin manager."""

from __future__ import annotations

import dataclasses as dc
import os
import textwrap
import typing as typ
from unittest import mock

import pytest

from cmd_mox import pytest_plugin
from cmd_mox.controller import Phase
from cmd_mox.environment import EnvironmentManager
from cmd_mox.pytest_plugin import STASH_CALL_FAILED, _CmdMoxManager
from tests.helpers.pytest_plugin_stubs import (
    ExitSpyCmdMoxManager,
    FixtureWithWrapped,
    RequestKwargs,
    StubConfig,
    StubMarker,
    StubMox,
    StubMoxBehaviorConfig,
    StubNode,
    StubRequest,
    assert_lifecycle_calls,
    assert_teardown_sections,
)

pytest_plugins = ("pytester",)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from _pytest.pytester import RunResult


@pytest.fixture
def reset_exit_spy_manager_state() -> cabc.Iterator[None]:
    """Clear the spy's shared instance reference before and after each test.

    Yields
    ------
    None
        Test execution between the initial reset and the teardown reset.
    """
    ExitSpyCmdMoxManager.last_instance = None
    yield
    ExitSpyCmdMoxManager.last_instance = None


@pytest.fixture
def make_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> cabc.Callable[..., _CmdMoxManager]:
    """Provide a factory building managers backed by a stub CmdMox.

    Returns
    -------
    cabc.Callable[..., _CmdMoxManager]
        Factory taking a stub request plus stub keyword arguments and
        returning a manager whose CmdMox dependency has been substituted.
    """

    def _make(request: StubRequest, **stub_kwargs: object) -> _CmdMoxManager:
        """Build a manager whose CmdMox is a stub configured by ``stub_kwargs``."""

        def _factory(
            *,
            verify_on_exit: bool = False,
            environment: EnvironmentManager | None = None,
            lifecycle_hint: str | None = None,
            fail_fast: bool = False,
        ) -> StubMox:
            """Build a stub CmdMox that accepts the real constructor keywords."""
            behavior_config = StubMoxBehaviorConfig(
                verify_on_exit=verify_on_exit,
                raise_on_exit=typ.cast("bool", stub_kwargs.get("raise_on_exit", False)),
                raise_on_verify=typ.cast(
                    "bool", stub_kwargs.get("raise_on_verify", False)
                ),
            )
            phase = typ.cast("Phase", stub_kwargs.get("phase", Phase.REPLAY))
            return StubMox(
                phase=phase,
                behavior=behavior_config,
                environment=environment,
                lifecycle_hint=lifecycle_hint,
                fail_fast=fail_fast,
            )

        monkeypatch.setattr(pytest_plugin, "CmdMox", _factory)
        manager = _CmdMoxManager(typ.cast("pytest.FixtureRequest", request))
        assert isinstance(manager.mox, StubMox), "Assertion failed"
        return manager

    return _make


@dc.dataclass(slots=True, frozen=True)
class _WorkerPrefixCase:
    """Input and expected result for worker-prefix generation."""

    env_var: str | None
    workerinput: object
    expected_prefix: str


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _WorkerPrefixCase(
                env_var=None,
                workerinput={"workerid": "gw-dict"},
                expected_prefix="cmdmox-gw-dict-",
            ),
            id="mapping-workerinput",
        ),
        pytest.param(
            _WorkerPrefixCase(
                env_var="env-worker",
                workerinput={"workerid": "gw-dict"},
                expected_prefix="cmdmox-env-worker-",
            ),
            id="env-override",
        ),
        pytest.param(
            _WorkerPrefixCase(
                env_var=None,
                workerinput=object(),
                expected_prefix="cmdmox-main-",
            ),
            id="unexpected-workerinput",
        ),
        pytest.param(
            _WorkerPrefixCase(
                env_var="env worker*!",
                workerinput={"workerid": "gw/unsafe"},
                expected_prefix="cmdmox-env-worker--",
            ),
            id="sanitised-worker",
        ),
    ],
)
def test_worker_prefix_generation(
    monkeypatch: pytest.MonkeyPatch,
    make_manager: cabc.Callable[..., _CmdMoxManager],
    case: _WorkerPrefixCase,
) -> None:
    """Ensure worker prefix generation from various input sources."""
    if case.env_var is None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    else:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", case.env_var)

    config = StubConfig(workerinput=case.workerinput)
    request = StubRequest(config=config)

    manager = make_manager(request)

    env = manager.mox.environment
    assert isinstance(env, EnvironmentManager), "Assertion failed"
    assert env._prefix.startswith(case.expected_prefix), "Assertion failed"


def test_cmd_mox_fixture_restores_path_on_replay_failure(
    pytester: pytest.Pytester,
    tmp_path: Path,
    run_nested_pytester: cabc.Callable[..., RunResult],
) -> None:
    """Fixture teardown restores PATH after replay raises during setup."""
    original_path = os.environ.get("PATH", "")
    dump_path = tmp_path / "path_snapshot.txt"

    test_module = textwrap.dedent(
        f"""
        import os
        import pytest
        from pathlib import Path

        from cmd_mox.controller import CmdMox

        pytest_plugins = ("cmd_mox.pytest_plugin",)

        PATH_DUMP = Path({str(dump_path)!r})

        @pytest.fixture(autouse=True)
        def break_replay(monkeypatch):
            def _boom(self):
                PATH_DUMP.write_text(os.environ.get("PATH", ""))
                raise RuntimeError("replay boom")

            monkeypatch.setattr(CmdMox, "replay", _boom)

        def test_replay_failure(cmd_mox):
            assert False, "fixture should error before running"
        """
    )

    pytester.makepyfile(test_module)
    result = run_nested_pytester(test_path=pytester.path)
    result.assert_outcomes(errors=1)

    assert dump_path.exists(), "Assertion failed"
    recorded_path = dump_path.read_text().strip()
    assert recorded_path != original_path, "Assertion failed"
    assert "cmdmox-" in recorded_path, "Assertion failed"
    assert os.environ.get("PATH", "") == original_path, "Assertion failed"


@pytest.mark.usefixtures("reset_exit_spy_manager_state")
def test_cmd_mox_fixture_marks_injected_keyboardinterrupt_as_body_failure(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """An injected generator interruption is recorded before propagation."""
    node = StubNode()
    request = StubRequest(config=StubConfig(), node=node)
    # Install the StubMox factory used when the fixture creates the spy manager.
    make_manager(request, raise_on_verify=True)

    with (
        mock.patch.object(pytest_plugin, "skip_if_unsupported", return_value=None),
        mock.patch.object(pytest_plugin, "_CmdMoxManager", ExitSpyCmdMoxManager),
    ):
        fixture = typ.cast("FixtureWithWrapped", pytest_plugin.cmd_mox).__wrapped__
        generator = fixture(typ.cast("pytest.FixtureRequest", request))
        next(generator)
        interruption = KeyboardInterrupt()

        with pytest.raises(KeyboardInterrupt) as raised:
            generator.throw(interruption)

    manager = ExitSpyCmdMoxManager.last_instance
    assert manager is not None, "Assertion failed"
    assert manager.body_failed_values == [True], "Assertion failed"
    assert raised.value is interruption, "Assertion failed"
    assert_teardown_sections(
        node,
        ("teardown", "cmd_mox verification", "RuntimeError: verify boom"),
    )


@pytest.mark.usefixtures("reset_exit_spy_manager_state")
@pytest.mark.parametrize(
    "interruption_type",
    [
        pytest.param(KeyboardInterrupt, id="keyboard-interrupt"),
        pytest.param(SystemExit, id="system-exit"),
    ],
)
def test_cmd_mox_fixture_marks_setup_baseexception_as_body_failure(
    make_manager: cabc.Callable[..., _CmdMoxManager],
    interruption_type: type[BaseException],
) -> None:
    """Setup interrupts are recorded before the fixture re-raises them."""
    request = StubRequest(config=StubConfig())
    make_manager(request)
    interruption = interruption_type()

    with (
        mock.patch.object(pytest_plugin, "skip_if_unsupported", return_value=None),
        mock.patch.object(pytest_plugin, "_CmdMoxManager", ExitSpyCmdMoxManager),
        mock.patch.object(ExitSpyCmdMoxManager, "enter", side_effect=interruption),
    ):
        fixture = typ.cast("FixtureWithWrapped", pytest_plugin.cmd_mox).__wrapped__
        generator = fixture(typ.cast("pytest.FixtureRequest", request))

        with pytest.raises(interruption_type) as raised:
            next(generator)

    manager = ExitSpyCmdMoxManager.last_instance
    assert manager is not None, "Assertion failed"
    assert manager.body_failed_values == [True], "Assertion failed"
    assert raised.value is interruption, "Assertion failed"


def test_enter_cmd_mox_replays_when_enabled(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Manager enters context and replays when auto lifecycle is enabled."""
    request = StubRequest(config=StubConfig())
    manager = make_manager(request)

    manager.enter()

    stub = typ.cast("StubMox", manager.mox)
    assert_lifecycle_calls(stub, enter=1, replay=1)


@pytest.mark.parametrize(
    "request_kwargs",
    [
        pytest.param(
            {"node": StubNode(marker=StubMarker(auto_lifecycle=False))},
            id="marker-override",
        ),
        pytest.param(
            {"param": {"auto_lifecycle": False}},
            id="param-override",
        ),
    ],
)
def test_enter_cmd_mox_auto_lifecycle_overrides(
    make_manager: cabc.Callable[..., _CmdMoxManager], request_kwargs: RequestKwargs
) -> None:
    """Marker and fixture parameter overrides disable automatic replay."""
    request = StubRequest(config=StubConfig(), **request_kwargs)
    manager = make_manager(request)

    assert not manager.auto_lifecycle, "Assertion failed"
    assert manager.mox._lifecycle_hint is None, "hint must be omitted in manual mode"

    manager.enter()

    stub = typ.cast("StubMox", manager.mox)
    assert_lifecycle_calls(stub, enter=1, replay=0)


def test_exit_cmd_mox_verifies_when_needed(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Replay phase triggers verification during teardown."""
    request = StubRequest(config=StubConfig())
    manager = make_manager(request)

    manager.enter()
    manager.exit(body_failed=False)

    stub = typ.cast("StubMox", manager.mox)
    assert stub.verify_calls == 1, "Assertion failed"
    assert stub.exit_calls == [(None, None, None)], "Assertion failed"


def test_exit_cmd_mox_skips_verification_when_phase_not_replay(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Manager avoids redundant verification once phase has advanced."""
    request = StubRequest(config=StubConfig())
    manager = make_manager(request)

    manager.enter()
    stub = typ.cast("StubMox", manager.mox)
    stub.phase = Phase.VERIFY

    manager.exit(body_failed=False)

    assert stub.verify_calls == 0, "Assertion failed"


def test_exit_cmd_mox_records_verify_error_when_test_failed(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Verification errors surface as teardown sections when body fails."""
    node = StubNode()
    request = StubRequest(config=StubConfig(), node=node)
    manager = make_manager(request, raise_on_verify=True)

    manager.enter()
    manager.exit(body_failed=True)

    stub = typ.cast("StubMox", manager.mox)
    assert stub.verify_calls == 1, "Assertion failed"
    assert_teardown_sections(
        node, ("teardown", "cmd_mox verification", "RuntimeError: verify boom")
    )


def test_exit_cmd_mox_records_verify_error_when_call_stage_failed(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Call-stage failure suppresses verify error and records a section."""
    node = StubNode()
    request = StubRequest(config=StubConfig(), node=node)
    manager = make_manager(request, raise_on_verify=True)

    manager.enter()
    # Simulate pytest_runtest_makereport storing call failure on the node.
    node.stash[STASH_CALL_FAILED] = True

    # Should not raise; error is suppressed and recorded as a teardown section.
    manager.exit(body_failed=False)

    stub = typ.cast("StubMox", manager.mox)
    assert stub.verify_calls == 1, "Assertion failed"
    assert_teardown_sections(
        node, ("teardown", "cmd_mox verification", "RuntimeError: verify boom")
    )
    # Stash flag is consumed and cleared
    assert STASH_CALL_FAILED not in node.stash, "Assertion failed"


def test_enter_cmd_mox_param_override_precedes_marker(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Fixture param takes precedence over marker configuration."""
    marker = StubMarker(auto_lifecycle=True)
    request = StubRequest(
        config=StubConfig(),
        node=StubNode(marker=marker),
        param={"auto_lifecycle": False},
    )
    manager = make_manager(request)

    assert not manager.auto_lifecycle, "Assertion failed"

    manager.enter()

    stub = typ.cast("StubMox", manager.mox)
    assert_lifecycle_calls(stub, enter=1, replay=0)


@dc.dataclass(slots=True, frozen=True)
class _PrecedenceCase:
    """One row of the fail-fast precedence truth table."""

    config: StubConfig
    marker_kwargs: dict[str, bool]
    param: dict[str, bool] | None
    expected: bool


def _precedence_case(
    config: StubConfig,
    *,
    marker_kwargs: dict[str, bool] | None = None,
    param: dict[str, bool] | None = None,
    expected: bool,
) -> _PrecedenceCase:
    """Build a precedence case, defaulting the unset sources."""
    return _PrecedenceCase(
        config=config,
        marker_kwargs={} if marker_kwargs is None else marker_kwargs,
        param=param,
        expected=expected,
    )


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(_precedence_case(StubConfig(), expected=False), id="default-off"),
        pytest.param(
            _precedence_case(StubConfig(fail_fast_ini=True), expected=True),
            id="ini-enables",
        ),
        pytest.param(
            _precedence_case(
                StubConfig(fail_fast_ini=False, fail_fast_cli=True), expected=True
            ),
            id="cli-over-ini",
        ),
        pytest.param(
            _precedence_case(
                StubConfig(fail_fast_ini=True, fail_fast_cli=False),
                marker_kwargs={"fail_fast": True},
                expected=True,
            ),
            id="marker-over-cli-and-ini",
        ),
        pytest.param(
            _precedence_case(
                StubConfig(fail_fast_ini=True, fail_fast_cli=True),
                marker_kwargs={"fail_fast": True},
                param={"fail_fast": False},
                expected=False,
            ),
            id="param-over-marker",
        ),
    ],
)
def test_fail_fast_resolution_precedence(
    make_manager: cabc.Callable[..., _CmdMoxManager],
    case: _PrecedenceCase,
) -> None:
    """Fail-fast resolves independently from parameter, marker, CLI, then ini."""
    request = StubRequest(
        config=case.config,
        node=StubNode(marker=StubMarker(**case.marker_kwargs)),
        param=case.param,
    )
    manager = make_manager(request)

    stub = typ.cast("StubMox", manager.mox)
    assert stub.fail_fast is case.expected, "fail_fast must follow the precedence order"


def test_fail_fast_only_param_keeps_lifecycle_default(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """A fail-fast-only parameter leaves auto lifecycle at its ini default."""
    request = StubRequest(config=StubConfig(), param={"fail_fast": True})
    manager = make_manager(request)

    stub = typ.cast("StubMox", manager.mox)
    assert manager.auto_lifecycle, "auto lifecycle must keep its ini default"
    assert stub.fail_fast, "fail_fast must be applied from the parameter"


def test_combined_param_dict_sets_both_flags(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """A parameter dictionary may configure both flags at once."""
    request = StubRequest(
        config=StubConfig(),
        param={"auto_lifecycle": False, "fail_fast": True},
    )
    manager = make_manager(request)

    stub = typ.cast("StubMox", manager.mox)
    assert not manager.auto_lifecycle, "auto lifecycle must be disabled"
    assert stub.fail_fast, "fail_fast must be enabled"


def test_param_dict_ignores_extra_keys(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Unrecognised keys are ignored once a recognised key is present."""
    request = StubRequest(
        config=StubConfig(),
        param={"fail_fast": True, "unexpected": True},
    )
    manager = make_manager(request)

    stub = typ.cast("StubMox", manager.mox)
    assert stub.fail_fast, "recognised keys must still be applied"


def test_param_dict_without_recognised_key_is_rejected(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """A dictionary with no recognised key remains a configuration error."""
    request = StubRequest(config=StubConfig(), param={"unexpected": True})

    with pytest.raises(TypeError, match="auto_lifecycle"):
        make_manager(request)


def test_auto_lifecycle_hint_explains_override_precedence(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """The lifecycle hint makes higher-precedence opt-outs clear."""
    request = StubRequest(
        config=StubConfig(cli=False, ini=False),
        node=StubNode(marker=StubMarker(auto_lifecycle=True)),
        param={"auto_lifecycle": True},
    )
    manager = make_manager(request)

    assert manager.auto_lifecycle, "Assertion failed"
    hint = manager.mox._lifecycle_hint
    expected_hint = (
        "Disable the most specific active setting: pass False (or "
        "{'auto_lifecycle': False}) as the cmd_mox fixture parameter, use "
        "@pytest.mark.cmd_mox(auto_lifecycle=False), pass "
        "--no-cmd-mox-auto-lifecycle, or set the "
        "cmd_mox_auto_lifecycle ini option to false. Fixture parameters "
        "override markers, markers override the CLI, and the CLI overrides "
        "the ini option."
    )
    assert hint == expected_hint, "auto lifecycle hint must explain precedence"


@pytest.mark.parametrize("mode", ["explicit-node", "request-node"])
def test_exit_cmd_mox_cleanup_error_handling(
    make_manager: cabc.Callable[..., _CmdMoxManager],
    mode: str,
) -> None:
    """Cleanup errors fail the test and emit teardown sections across scenarios."""
    if mode == "explicit-node":
        node = StubNode()
        request = StubRequest(config=StubConfig(), node=node)
    else:
        request = StubRequest(config=StubConfig())
        node = request.node

    manager = make_manager(request, raise_on_exit=True)

    manager.enter()

    with pytest.raises(pytest.fail.Exception) as excinfo:
        manager.exit(body_failed=True)

    message = str(excinfo.value)
    assert "cmd_mox fixture cleanup failed" in message, "Assertion failed"
    assert node.nodeid in message, "Assertion failed"
    assert "OSError: exit boom" in message, "Assertion failed"
    assert_teardown_sections(
        node, ("teardown", "cmd_mox cleanup", "OSError: exit boom")
    )


def test_exit_cmd_mox_fails_on_verify_error_when_body_passes(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Verification errors fail the test when the body succeeded."""
    request = StubRequest(config=StubConfig())
    node = request.node
    manager = make_manager(request, raise_on_verify=True)

    manager.enter()

    with pytest.raises(pytest.fail.Exception) as excinfo:
        manager.exit(body_failed=False)

    expected = f"cmd_mox verification for {node.nodeid} RuntimeError: verify boom"
    assert expected in str(excinfo.value), "Assertion failed"
    assert_teardown_sections(
        node, ("teardown", "cmd_mox verification", "RuntimeError: verify boom")
    )


def test_exit_cmd_mox_reports_both_verify_and_cleanup_errors(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Combined teardown failures report both verification and cleanup issues."""
    request = StubRequest(config=StubConfig())
    node = request.node
    manager = make_manager(request, raise_on_exit=True, raise_on_verify=True)

    manager.enter()

    with pytest.raises(pytest.fail.Exception) as excinfo:
        manager.exit(body_failed=False)

    message = str(excinfo.value)
    assert "verification RuntimeError: verify boom" in message, "Assertion failed"
    assert f"cleanup for {node.nodeid} OSError: exit boom" in message, (
        "Assertion failed"
    )
    assert_teardown_sections(
        node,
        ("teardown", "cmd_mox verification", "RuntimeError: verify boom"),
        ("teardown", "cmd_mox cleanup", "OSError: exit boom"),
    )


def test_exit_cmd_mox_logs_verification_context(
    make_manager: cabc.Callable[..., _CmdMoxManager],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verification failures include node context in logs."""
    caplog.set_level("ERROR")
    request = StubRequest(config=StubConfig())
    manager = make_manager(request, raise_on_verify=True)

    manager.enter()

    with pytest.raises(pytest.fail.Exception):
        manager.exit(body_failed=False)

    message = f"cmd_mox verification failed for {request.node.nodeid}"
    assert message in caplog.text, "Assertion failed"


def test_exit_cmd_mox_logs_cleanup_context(
    make_manager: cabc.Callable[..., _CmdMoxManager],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cleanup failures include node context in logs."""
    caplog.set_level("ERROR")
    request = StubRequest(config=StubConfig())
    manager = make_manager(request, raise_on_exit=True)

    manager.enter()

    with pytest.raises(pytest.fail.Exception):
        manager.exit(body_failed=False)

    message = f"Error during cmd_mox fixture cleanup for {request.node.nodeid}"
    assert message in caplog.text, "Assertion failed"


def test_exit_cmd_mox_is_idempotent_without_enter(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Calling exit before a successful enter is a no-op."""
    request = StubRequest(config=StubConfig())
    manager = make_manager(request)

    manager.exit(body_failed=False)

    stub = typ.cast("StubMox", manager.mox)
    assert stub.exit_calls == [], "Assertion failed"


def test_exit_cmd_mox_is_idempotent_after_teardown(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Repeated exit calls after teardown keep succeeding."""
    request = StubRequest(config=StubConfig())
    manager = make_manager(request)

    manager.enter()
    manager.exit(body_failed=False)

    # A second exit should be a no-op and not trigger additional cleanup.
    manager.exit(body_failed=False)

    stub = typ.cast("StubMox", manager.mox)
    assert len(stub.exit_calls) == 1, "Assertion failed"
