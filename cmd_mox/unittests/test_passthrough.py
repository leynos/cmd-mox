"""Unit tests for passthrough coordination behaviour."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.ipc import Invocation, PassthroughResult
from cmd_mox.passthrough import PassthroughCoordinator

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from cmd_mox.test_doubles import CommandDouble


class _FakeExpectation:
    def __init__(self, env: dict[str, str]) -> None:
        self.env = env


class _FakeDouble:
    def __init__(self, env: dict[str, str]) -> None:
        self.expectation = _FakeExpectation(env)


def _make_invocation(command: str = "echo") -> Invocation:
    return Invocation(command=command, args=["hi"], stdin="", env={})


def test_prepare_request_registers_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coordinator should record invocations until results arrive."""
    coordinator = PassthroughCoordinator()
    double = t.cast("CommandDouble", _FakeDouble({"PATH": "/usr/bin"}))
    invocation = _make_invocation()

    response = coordinator.prepare_request(double, invocation, "/usr/bin", timeout=2.0)

    assert response.passthrough is not None
    assert response.passthrough.lookup_path == "/usr/bin"
    assert response.passthrough.timeout == 2.0
    assert response.passthrough.extra_env == {"PATH": "/usr/bin"}
    assert response.env == {"PATH": "/usr/bin"}
    assert coordinator.has_pending(invocation.invocation_id or "")


def test_prepare_request_merges_extra_env() -> None:
    """Extra env should extend the expectation mapping without replacing it."""
    coordinator = PassthroughCoordinator()
    double = t.cast("CommandDouble", _FakeDouble({"ALPHA": "1"}))
    invocation = _make_invocation()

    response = coordinator.prepare_request(
        double,
        invocation,
        "/bin",
        timeout=1.0,
        extra_env={"BETA": "2"},
    )

    assert response.passthrough is not None
    assert response.passthrough.extra_env == {"ALPHA": "1", "BETA": "2"}
    assert response.env == {"ALPHA": "1", "BETA": "2"}


def test_prepare_request_extra_env_overrides_keys() -> None:
    """Extra env should override expectation values when keys overlap."""
    coordinator = PassthroughCoordinator()
    double = t.cast("CommandDouble", _FakeDouble({"SHARED": "expected"}))
    invocation = _make_invocation()

    response = coordinator.prepare_request(
        double,
        invocation,
        "/bin",
        timeout=1.0,
        extra_env={"SHARED": "override"},
    )

    assert response.passthrough is not None
    assert response.passthrough.extra_env == {"SHARED": "override"}
    assert response.env == {"SHARED": "override"}


def test_finalize_result_returns_response_and_clears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Finalisation should return stored data and clear pending state."""
    coordinator = PassthroughCoordinator()
    double = t.cast("CommandDouble", _FakeDouble({"EXTRA": "1"}))
    invocation = _make_invocation("tool")
    invocation.env.update({"EXTRA": "1"})
    response = coordinator.prepare_request(double, invocation, "/opt/bin", timeout=1.0)
    directive = response.passthrough
    assert directive is not None

    result = PassthroughResult(
        invocation_id=directive.invocation_id,
        stdout="out",
        stderr="err",
        exit_code=3,
    )

    resolved_double, stored_invocation, final_response = coordinator.finalize_result(
        result
    )

    assert resolved_double is double
    assert stored_invocation.command == "tool"
    assert final_response.exit_code == 3
    assert final_response.env == {"EXTRA": "1"}
    assert stored_invocation.env == {"EXTRA": "1"}
    assert not coordinator.has_pending(directive.invocation_id)


def test_finalize_result_rejects_unknown_invocation() -> None:
    """Unexpected invocation IDs should raise a RuntimeError."""
    coordinator = PassthroughCoordinator()

    with pytest.raises(RuntimeError, match="Unexpected passthrough result"):
        coordinator.finalize_result(
            PassthroughResult(
                invocation_id="missing",
                stdout="",
                stderr="",
                exit_code=0,
            )
        )


def test_expired_requests_are_pruned(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requests past their TTL should be purged on subsequent activity."""
    now = 100.0

    def fake_monotonic() -> float:
        return now

    monkeypatch.setattr("cmd_mox.passthrough.time.monotonic", fake_monotonic)

    coordinator = PassthroughCoordinator(cleanup_ttl=5.0)
    double = t.cast("CommandDouble", _FakeDouble({}))
    invocation = _make_invocation("slow")
    directive = coordinator.prepare_request(double, invocation, "/bin", timeout=1.0)
    assert directive.passthrough is not None
    pending_id = directive.passthrough.invocation_id
    assert coordinator.has_pending(pending_id)

    now += 10.0  # advance beyond cleanup TTL
    assert not coordinator.has_pending(pending_id)
    assert coordinator.pending_count() == 0
