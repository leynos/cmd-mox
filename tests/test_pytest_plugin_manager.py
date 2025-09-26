"""Unit tests for the internal cmd_mox pytest plugin manager."""

from __future__ import annotations

import os
import textwrap
import typing as t

import pytest

from cmd_mox import pytest_plugin
from cmd_mox.controller import Phase
from cmd_mox.environment import EnvironmentManager
from cmd_mox.pytest_plugin import STASH_CALL_FAILED, _CmdMoxManager

if t.TYPE_CHECKING:
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
        assert name == "cmd_mox_auto_lifecycle"
        return self._cli

    def getini(self, name: str) -> bool:
        assert name == "cmd_mox_auto_lifecycle"
        return self._ini


class _StubMarker:
    """Simple marker surrogate exposing keyword arguments."""

    __slots__ = ("kwargs",)

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _StubNode:
    """Minimal pytest node supporting markers and report sections."""

    __slots__ = ("_marker", "sections", "stash")

    def __init__(self, marker: _StubMarker | None = None) -> None:
        self._marker = marker
        self.sections: list[tuple[str, str, str]] = []
        self.stash = pytest.Stash()

    def get_closest_marker(self, name: str) -> _StubMarker | None:
        return self._marker if name == "cmd_mox" else None

    def add_report_section(self, when: str, key: str, content: str) -> None:
        self.sections.append((when, key, content))


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


class _StubMox:
    """Minimal CmdMox stand-in for exercising manager behaviour."""

    def __init__(
        self,
        *,
        phase: Phase = Phase.REPLAY,
        raise_on_exit: bool = False,
        raise_on_verify: bool = False,
        verify_on_exit: bool = False,
    ) -> None:
        self.phase = phase
        self.raise_on_exit = raise_on_exit
        self.raise_on_verify = raise_on_verify
        self.verify_on_exit = verify_on_exit
        self.enter_calls = 0
        self.replay_calls = 0
        self.verify_calls = 0
        self.exit_calls: list[tuple[object | None, object | None, object | None]] = []
        self.environment: object | None = None

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


def _make_manager(
    monkeypatch: pytest.MonkeyPatch,
    request: _StubRequest,
    **stub_kwargs: object,
) -> _CmdMoxManager:
    """Instantiate a manager while substituting the CmdMox dependency."""

    def _factory(*, verify_on_exit: bool = False) -> _StubMox:
        return _StubMox(verify_on_exit=verify_on_exit, **stub_kwargs)

    monkeypatch.setattr(pytest_plugin, "CmdMox", _factory)
    manager = _CmdMoxManager(t.cast("pytest.FixtureRequest", request))
    assert isinstance(manager.mox, _StubMox)
    return manager


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
    ],
)
def test_worker_prefix_generation(
    monkeypatch: pytest.MonkeyPatch,
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

    manager = _make_manager(monkeypatch, request)

    env = manager.mox.environment
    assert isinstance(env, EnvironmentManager)
    assert env._prefix.startswith(expected_prefix)


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

    assert dump_path.exists()
    recorded_path = dump_path.read_text().strip()
    assert recorded_path != original_path
    assert "cmdmox-" in recorded_path
    assert os.environ.get("PATH", "") == original_path


def test_enter_cmd_mox_replays_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Manager enters context and replays when auto lifecycle is enabled."""
    request = _StubRequest(config=_StubConfig())
    manager = _make_manager(monkeypatch, request)

    manager.enter()

    stub = t.cast("_StubMox", manager.mox)
    assert stub.enter_calls == 1
    assert stub.replay_calls == 1


def test_enter_cmd_mox_skips_replay_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Marker override disables automatic replay."""
    marker = _StubMarker(auto_lifecycle=False)
    request = _StubRequest(config=_StubConfig(), node=_StubNode(marker=marker))
    manager = _make_manager(monkeypatch, request)

    assert not manager.auto_lifecycle

    manager.enter()

    stub = t.cast("_StubMox", manager.mox)
    assert stub.enter_calls == 1
    assert stub.replay_calls == 0


def test_exit_cmd_mox_verifies_when_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay phase triggers verification during teardown."""
    request = _StubRequest(config=_StubConfig())
    manager = _make_manager(monkeypatch, request)

    manager.enter()
    manager.exit(body_failed=False)

    stub = t.cast("_StubMox", manager.mox)
    assert stub.verify_calls == 1
    assert stub.exit_calls == [(None, None, None)]


def test_exit_cmd_mox_skips_verification_when_phase_not_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manager avoids redundant verification once phase has advanced."""
    request = _StubRequest(config=_StubConfig())
    manager = _make_manager(monkeypatch, request)

    manager.enter()
    stub = t.cast("_StubMox", manager.mox)
    stub.phase = Phase.VERIFY

    manager.exit(body_failed=False)

    assert stub.verify_calls == 0


def test_exit_cmd_mox_records_verify_error_when_test_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verification errors surface as teardown sections when body fails."""
    node = _StubNode()
    request = _StubRequest(config=_StubConfig(), node=node)
    manager = _make_manager(monkeypatch, request, raise_on_verify=True)

    manager.enter()
    manager.exit(body_failed=True)

    stub = t.cast("_StubMox", manager.mox)
    assert stub.verify_calls == 1
    assert node.sections == [
        ("teardown", "cmd_mox verification", "RuntimeError: verify boom")
    ]


def test_exit_cmd_mox_records_verify_error_when_call_stage_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Call-stage failure suppresses verify error and records a section."""
    node = _StubNode()
    request = _StubRequest(config=_StubConfig(), node=node)
    manager = _make_manager(monkeypatch, request, raise_on_verify=True)

    manager.enter()
    # Simulate pytest_runtest_makereport storing call failure on the node.
    node.stash[STASH_CALL_FAILED] = True

    # Should not raise; error is suppressed and recorded as a teardown section.
    manager.exit(body_failed=False)

    stub = t.cast("_StubMox", manager.mox)
    assert stub.verify_calls == 1
    assert node.sections == [
        ("teardown", "cmd_mox verification", "RuntimeError: verify boom")
    ]


def test_exit_cmd_mox_fails_on_verify_error_when_body_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verification errors fail the test when the body succeeded."""
    request = _StubRequest(config=_StubConfig())
    manager = _make_manager(monkeypatch, request, raise_on_verify=True)

    manager.enter()

    with pytest.raises(pytest.fail.Exception) as excinfo:
        manager.exit(body_failed=False)

    assert "cmd_mox verification RuntimeError: verify boom" in str(excinfo.value)


def test_exit_cmd_mox_fails_on_cleanup_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cleanup errors always fail the test."""
    request = _StubRequest(config=_StubConfig())
    manager = _make_manager(monkeypatch, request, raise_on_exit=True)

    manager.enter()

    with pytest.raises(pytest.fail.Exception) as excinfo:
        manager.exit(body_failed=True)

    message = str(excinfo.value)
    assert "cmd_mox cleanup OSError: exit boom" in message


def test_exit_cmd_mox_reports_both_verify_and_cleanup_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Combined teardown failures report both verification and cleanup issues."""
    request = _StubRequest(config=_StubConfig())
    manager = _make_manager(
        monkeypatch, request, raise_on_exit=True, raise_on_verify=True
    )

    manager.enter()

    with pytest.raises(pytest.fail.Exception) as excinfo:
        manager.exit(body_failed=False)

    message = str(excinfo.value)
    assert "verification RuntimeError: verify boom" in message
    assert "cleanup OSError: exit boom" in message


def test_exit_cmd_mox_is_idempotent_without_enter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Calling exit before a successful enter is a no-op."""
    request = _StubRequest(config=_StubConfig())
    manager = _make_manager(monkeypatch, request)

    manager.exit(body_failed=False)

    stub = t.cast("_StubMox", manager.mox)
    assert stub.exit_calls == []
