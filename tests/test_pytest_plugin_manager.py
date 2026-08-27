"""Unit tests for the internal cmd_mox pytest plugin manager."""

from __future__ import annotations

import dataclasses as dc
import os
import textwrap
import typing as typ

import pytest

from cmd_mox import pytest_plugin
from cmd_mox.controller import Phase
from cmd_mox.environment import EnvironmentManager
from cmd_mox.pytest_plugin import STASH_CALL_FAILED, _CmdMoxManager

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

_VERIFY_ERROR_MESSAGE = "verify boom"
_EXIT_ERROR_MESSAGE = "exit boom"


class _StubConfig:
    """Mimic ``pytest.Config`` with controllable lifecycle settings."""

    __slots__ = ("_cli", "_ini", "workerinput")

    def __init__(
        self,
        *,
        workerinput: object | None = None,
        cli: bool | None = None,
        ini: bool = True,
    ) -> None:
        self.workerinput = workerinput
        self._cli = cli
        self._ini = ini

    def getoption(self, name: str) -> bool | None:
        assert name == "cmd_mox_auto_lifecycle", "Assertion failed"
        return self._cli

    def getini(self, name: str) -> bool:
        assert name == "cmd_mox_auto_lifecycle", "Assertion failed"
        return self._ini


class _StubMarker:
    """Simple marker surrogate exposing keyword arguments."""

    __slots__ = ("kwargs",)

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _StubNode:
    """Minimal pytest node supporting markers and report sections."""

    __slots__ = ("_marker", "nodeid", "sections", "stash")

    def __init__(
        self, marker: _StubMarker | None = None, nodeid: str = "test::stub"
    ) -> None:
        self._marker = marker
        self.sections: list[tuple[str, str, str]] = []
        self.stash = pytest.Stash()
        self.nodeid = nodeid

    def get_closest_marker(self, name: str) -> _StubMarker | None:
        return self._marker if name == "cmd_mox" else None

    def add_report_section(self, when: str, key: str, content: str) -> None:
        self.sections.append((when, key, content))


class _RequestKwargs(typ.TypedDict, total=False):
    """Keyword arguments forwarded into ``_StubRequest``."""

    node: _StubNode
    param: dict[str, bool]


class _StubRequest:
    """Minimal fixture request exposing ``config``/``node``/``param``."""

    __slots__ = ("config", "node", "param")

    def __init__(
        self,
        *,
        config: _StubConfig,
        node: _StubNode | None = None,
        param: object | None = None,
    ) -> None:
        self.config = config
        self.node = node or _StubNode()
        if param is not None:
            self.param = param


@dc.dataclass(slots=True)
class StubMoxBehaviorConfig:
    """Configuration for _StubMox behavioral flags."""

    raise_on_exit: bool = False
    raise_on_verify: bool = False
    verify_on_exit: bool = False


class _StubMox:
    """Minimal CmdMox stand-in for exercising manager behaviour."""

    def __init__(
        self,
        *,
        phase: Phase = Phase.REPLAY,
        behavior: StubMoxBehaviorConfig | None = None,
        environment: EnvironmentManager | None = None,
    ) -> None:
        self.phase = phase
        config = behavior or StubMoxBehaviorConfig()
        self.raise_on_exit = config.raise_on_exit
        self.raise_on_verify = config.raise_on_verify
        self.verify_on_exit = config.verify_on_exit
        self.enter_calls = 0
        self.replay_calls = 0
        self.verify_calls = 0
        self.exit_calls: list[tuple[object | None, object | None, object | None]] = []
        self.environment: object | None = environment

    def __enter__(self) -> _StubMox:
        self.enter_calls += 1
        return self

    def replay(self) -> None:
        self.replay_calls += 1
        self.phase = Phase.REPLAY

    def verify(self) -> None:
        self.verify_calls += 1
        if self.raise_on_verify:
            raise RuntimeError(_VERIFY_ERROR_MESSAGE)
        self.phase = Phase.VERIFY

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        self.exit_calls.append((exc_type, exc, tb))
        if self.raise_on_exit:
            raise OSError(_EXIT_ERROR_MESSAGE)
        self.phase = Phase.VERIFY


def _assert_teardown_sections(node: _StubNode, *expected: tuple[str, str, str]) -> None:
    """Assert the node recorded exactly the expected teardown report sections."""
    assert node.sections == list(expected), "Assertion failed"


def _assert_lifecycle_calls(stub: _StubMox, *, enter: int, replay: int) -> None:
    """Assert the stub recorded the expected enter and replay call counts."""
    assert stub.enter_calls == enter, "Assertion failed"
    assert stub.replay_calls == replay, "Assertion failed"


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

    def _make(request: _StubRequest, **stub_kwargs: object) -> _CmdMoxManager:
        def _factory(
            *,
            verify_on_exit: bool = False,
            environment: EnvironmentManager | None = None,
        ) -> _StubMox:
            # Keep signature compatible with real CmdMox; forward kwargs to stub.
            behavior_config = StubMoxBehaviorConfig(
                verify_on_exit=verify_on_exit,
                raise_on_exit=typ.cast("bool", stub_kwargs.get("raise_on_exit", False)),
                raise_on_verify=typ.cast(
                    "bool", stub_kwargs.get("raise_on_verify", False)
                ),
            )
            phase = typ.cast("Phase", stub_kwargs.get("phase", Phase.REPLAY))
            return _StubMox(
                phase=phase,
                behavior=behavior_config,
                environment=environment,
            )

        monkeypatch.setattr(pytest_plugin, "CmdMox", _factory)
        manager = _CmdMoxManager(typ.cast("pytest.FixtureRequest", request))
        assert isinstance(manager.mox, _StubMox), "Assertion failed"
        return manager

    return _make


@pytest.mark.parametrize(
    ("env_var", "workerinput", "expected_prefix"),
    [
        pytest.param(
            None,
            {"workerid": "gw-dict"},
            "cmdmox-gw-dict-",
            id="mapping-workerinput",
        ),
        pytest.param(
            "env-worker",
            {"workerid": "gw-dict"},
            "cmdmox-env-worker-",
            id="env-override",
        ),
        pytest.param(
            None,
            object(),
            "cmdmox-main-",
            id="unexpected-workerinput",
        ),
        pytest.param(
            "env worker*!",
            {"workerid": "gw/unsafe"},
            "cmdmox-env-worker--",
            id="sanitised-worker",
        ),
    ],
)
def test_worker_prefix_generation(
    monkeypatch: pytest.MonkeyPatch,
    make_manager: cabc.Callable[..., _CmdMoxManager],
    env_var: str | None,
    workerinput: object,
    expected_prefix: str,
) -> None:
    """Ensure worker prefix generation from various input sources."""
    if env_var is None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    else:
        monkeypatch.setenv("PYTEST_XDIST_WORKER", env_var)

    config = _StubConfig(workerinput=workerinput)
    request = _StubRequest(config=config)

    manager = make_manager(request)

    env = manager.mox.environment
    assert isinstance(env, EnvironmentManager), "Assertion failed"
    assert env._prefix.startswith(expected_prefix), "Assertion failed"


def test_cmd_mox_fixture_restores_path_on_replay_failure(
    pytester: pytest.Pytester, tmp_path: Path
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
    result = pytester.runpytest_inprocess()
    result.assert_outcomes(errors=1)

    assert dump_path.exists(), "Assertion failed"
    recorded_path = dump_path.read_text().strip()
    assert recorded_path != original_path, "Assertion failed"
    assert "cmdmox-" in recorded_path, "Assertion failed"
    assert os.environ.get("PATH", "") == original_path, "Assertion failed"


def test_enter_cmd_mox_replays_when_enabled(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Manager enters context and replays when auto lifecycle is enabled."""
    request = _StubRequest(config=_StubConfig())
    manager = make_manager(request)

    manager.enter()

    stub = typ.cast("_StubMox", manager.mox)
    _assert_lifecycle_calls(stub, enter=1, replay=1)


@pytest.mark.parametrize(
    "request_kwargs",
    [
        pytest.param(
            {"node": _StubNode(marker=_StubMarker(auto_lifecycle=False))},
            id="marker-override",
        ),
        pytest.param(
            {"param": {"auto_lifecycle": False}},
            id="param-override",
        ),
    ],
)
def test_enter_cmd_mox_auto_lifecycle_overrides(
    make_manager: cabc.Callable[..., _CmdMoxManager], request_kwargs: _RequestKwargs
) -> None:
    """Marker and fixture parameter overrides disable automatic replay."""
    request = _StubRequest(config=_StubConfig(), **request_kwargs)
    manager = make_manager(request)

    assert not manager.auto_lifecycle, "Assertion failed"

    manager.enter()

    stub = typ.cast("_StubMox", manager.mox)
    _assert_lifecycle_calls(stub, enter=1, replay=0)


def test_exit_cmd_mox_verifies_when_needed(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Replay phase triggers verification during teardown."""
    request = _StubRequest(config=_StubConfig())
    manager = make_manager(request)

    manager.enter()
    manager.exit(body_failed=False)

    stub = typ.cast("_StubMox", manager.mox)
    assert stub.verify_calls == 1, "Assertion failed"
    assert stub.exit_calls == [(None, None, None)], "Assertion failed"


def test_exit_cmd_mox_skips_verification_when_phase_not_replay(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Manager avoids redundant verification once phase has advanced."""
    request = _StubRequest(config=_StubConfig())
    manager = make_manager(request)

    manager.enter()
    stub = typ.cast("_StubMox", manager.mox)
    stub.phase = Phase.VERIFY

    manager.exit(body_failed=False)

    assert stub.verify_calls == 0, "Assertion failed"


def test_exit_cmd_mox_records_verify_error_when_test_failed(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Verification errors surface as teardown sections when body fails."""
    node = _StubNode()
    request = _StubRequest(config=_StubConfig(), node=node)
    manager = make_manager(request, raise_on_verify=True)

    manager.enter()
    manager.exit(body_failed=True)

    stub = typ.cast("_StubMox", manager.mox)
    assert stub.verify_calls == 1, "Assertion failed"
    _assert_teardown_sections(
        node, ("teardown", "cmd_mox verification", "RuntimeError: verify boom")
    )


def test_exit_cmd_mox_records_verify_error_when_call_stage_failed(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Call-stage failure suppresses verify error and records a section."""
    node = _StubNode()
    request = _StubRequest(config=_StubConfig(), node=node)
    manager = make_manager(request, raise_on_verify=True)

    manager.enter()
    # Simulate pytest_runtest_makereport storing call failure on the node.
    node.stash[STASH_CALL_FAILED] = True

    # Should not raise; error is suppressed and recorded as a teardown section.
    manager.exit(body_failed=False)

    stub = typ.cast("_StubMox", manager.mox)
    assert stub.verify_calls == 1, "Assertion failed"
    _assert_teardown_sections(
        node, ("teardown", "cmd_mox verification", "RuntimeError: verify boom")
    )
    # Stash flag is consumed and cleared
    assert STASH_CALL_FAILED not in node.stash, "Assertion failed"


def test_enter_cmd_mox_param_override_precedes_marker(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Fixture param takes precedence over marker configuration."""
    marker = _StubMarker(auto_lifecycle=True)
    request = _StubRequest(
        config=_StubConfig(),
        node=_StubNode(marker=marker),
        param={"auto_lifecycle": False},
    )
    manager = make_manager(request)

    assert not manager.auto_lifecycle, "Assertion failed"

    manager.enter()

    stub = typ.cast("_StubMox", manager.mox)
    _assert_lifecycle_calls(stub, enter=1, replay=0)


@pytest.mark.parametrize("mode", ["explicit-node", "request-node"])
def test_exit_cmd_mox_cleanup_error_handling(
    make_manager: cabc.Callable[..., _CmdMoxManager],
    mode: str,
) -> None:
    """Cleanup errors fail the test and emit teardown sections across scenarios."""
    if mode == "explicit-node":
        node = _StubNode()
        request = _StubRequest(config=_StubConfig(), node=node)
    else:
        request = _StubRequest(config=_StubConfig())
        node = request.node

    manager = make_manager(request, raise_on_exit=True)

    manager.enter()

    with pytest.raises(pytest.fail.Exception) as excinfo:
        manager.exit(body_failed=True)

    message = str(excinfo.value)
    assert "cmd_mox fixture cleanup failed" in message, "Assertion failed"
    assert node.nodeid in message, "Assertion failed"
    assert "OSError: exit boom" in message, "Assertion failed"
    _assert_teardown_sections(
        node, ("teardown", "cmd_mox cleanup", "OSError: exit boom")
    )


def test_exit_cmd_mox_fails_on_verify_error_when_body_passes(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Verification errors fail the test when the body succeeded."""
    request = _StubRequest(config=_StubConfig())
    node = request.node
    manager = make_manager(request, raise_on_verify=True)

    manager.enter()

    with pytest.raises(pytest.fail.Exception) as excinfo:
        manager.exit(body_failed=False)

    expected = f"cmd_mox verification for {node.nodeid} RuntimeError: verify boom"
    assert expected in str(excinfo.value), "Assertion failed"
    _assert_teardown_sections(
        node, ("teardown", "cmd_mox verification", "RuntimeError: verify boom")
    )


def test_exit_cmd_mox_reports_both_verify_and_cleanup_errors(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Combined teardown failures report both verification and cleanup issues."""
    request = _StubRequest(config=_StubConfig())
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
    _assert_teardown_sections(
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
    request = _StubRequest(config=_StubConfig())
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
    request = _StubRequest(config=_StubConfig())
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
    request = _StubRequest(config=_StubConfig())
    manager = make_manager(request)

    manager.exit(body_failed=False)

    stub = typ.cast("_StubMox", manager.mox)
    assert stub.exit_calls == [], "Assertion failed"


def test_exit_cmd_mox_is_idempotent_after_teardown(
    make_manager: cabc.Callable[..., _CmdMoxManager],
) -> None:
    """Repeated exit calls after teardown keep succeeding."""
    request = _StubRequest(config=_StubConfig())
    manager = make_manager(request)

    manager.enter()
    manager.exit(body_failed=False)

    # A second exit should be a no-op and not trigger additional cleanup.
    manager.exit(body_failed=False)

    stub = typ.cast("_StubMox", manager.mox)
    assert len(stub.exit_calls) == 1, "Assertion failed"
