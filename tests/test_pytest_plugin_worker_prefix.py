"""Regression tests for the top-level pytest plugin helpers."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.pytest_plugin import (
    _attach_node_state,
    _detach_node_state,
    _enter_cmd_mox,
    _teardown_cmd_mox,
    _worker_prefix,
)

if t.TYPE_CHECKING:  # pragma: no cover - used for typing only
    from cmd_mox.controller import CmdMox


class _StubConfig:
    """Mimic ``pytest.Config`` with a customizable ``workerinput`` attribute."""

    __slots__ = ("workerinput",)

    def __init__(self, workerinput: object | None = None) -> None:
        self.workerinput = workerinput


class _StubRequest:
    """Minimal request stub exposing the ``config`` attribute."""

    __slots__ = ("config",)

    def __init__(self, config: _StubConfig) -> None:
        self.config = config


class _StubMox:
    """Minimal CmdMox stand-in for exercising helper logic."""

    def __init__(self, *, raise_on_exit: bool = False) -> None:
        self.raise_on_exit = raise_on_exit
        self.enter_calls = 0
        self.replay_calls = 0
        self.exit_calls: list[tuple[object | None, object | None, object | None]] = []

    def __enter__(self) -> _StubMox:
        self.enter_calls += 1
        return self

    def replay(self) -> None:
        self.replay_calls += 1

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        self.exit_calls.append((exc_type, exc, tb))
        if self.raise_on_exit:
            raise OSError("boom")


class _StubItem:
    """Simplified pytest item supporting pytest-specific attributes."""

    _cmd_mox_instance: object | None
    _cmd_mox_auto_lifecycle: bool | None


def test_worker_prefix_uses_mapping_workerinput(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure xdist-style dict payloads produce unique worker prefixes."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    request = _StubRequest(config=_StubConfig(workerinput={"workerid": "gw-dict"}))

    prefix = _worker_prefix(request)  # type: ignore[arg-type]

    assert prefix.startswith("cmdmox-gw-dict-")


def test_worker_prefix_with_none_workerinput(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None workerinput falls back to the default worker prefix."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    request = _StubRequest(config=_StubConfig(workerinput=None))

    prefix = _worker_prefix(request)  # type: ignore[arg-type]

    assert prefix.startswith("cmdmox-main-")


def test_worker_prefix_with_unexpected_workerinput_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected workerinput types do not break prefix generation."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    request = _StubRequest(config=_StubConfig(workerinput=42))

    prefix = _worker_prefix(request)  # type: ignore[arg-type]

    assert prefix.startswith("cmdmox-main-")


def test_enter_cmd_mox_replays_when_enabled() -> None:
    """_enter_cmd_mox triggers replay when auto lifecycle is enabled."""
    stub = _StubMox()

    _enter_cmd_mox(t.cast("CmdMox", stub), auto_lifecycle=True)

    assert stub.enter_calls == 1
    assert stub.replay_calls == 1


def test_enter_cmd_mox_skips_replay_when_disabled() -> None:
    """_enter_cmd_mox only enters the context when auto lifecycle is off."""
    stub = _StubMox()

    _enter_cmd_mox(t.cast("CmdMox", stub), auto_lifecycle=False)

    assert stub.enter_calls == 1
    assert stub.replay_calls == 0


def test_attach_and_detach_node_state() -> None:
    """Attachment stores state on the item and detachment removes it."""
    item = _StubItem()
    stub = _StubMox()

    _attach_node_state(
        t.cast("pytest.Item", item), t.cast("CmdMox", stub), auto_lifecycle=True
    )

    assert item._cmd_mox_instance is stub
    assert item._cmd_mox_auto_lifecycle is True

    _detach_node_state(t.cast("pytest.Item", item), t.cast("CmdMox", stub))

    assert not hasattr(item, "_cmd_mox_instance")
    assert not hasattr(item, "_cmd_mox_auto_lifecycle")


def test_teardown_cmd_mox_calls_exit_and_detaches() -> None:
    """_teardown_cmd_mox exits the context and clears stored state."""
    item = _StubItem()
    stub = _StubMox()
    _attach_node_state(
        t.cast("pytest.Item", item), t.cast("CmdMox", stub), auto_lifecycle=False
    )

    _teardown_cmd_mox(t.cast("pytest.Item", item), t.cast("CmdMox", stub))

    assert stub.exit_calls == [(None, None, None)]
    assert not hasattr(item, "_cmd_mox_instance")
    assert not hasattr(item, "_cmd_mox_auto_lifecycle")


def test_teardown_cmd_mox_raises_pytest_fail_on_oserror() -> None:
    """OSError during teardown surfaces as a pytest failure."""
    item = _StubItem()
    stub = _StubMox(raise_on_exit=True)
    _attach_node_state(
        t.cast("pytest.Item", item), t.cast("CmdMox", stub), auto_lifecycle=True
    )

    with pytest.raises(pytest.fail.Exception):
        _teardown_cmd_mox(t.cast("pytest.Item", item), t.cast("CmdMox", stub))

    assert not hasattr(item, "_cmd_mox_instance")
    assert not hasattr(item, "_cmd_mox_auto_lifecycle")
