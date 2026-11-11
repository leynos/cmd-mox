"""Unit tests for passthrough coordination behaviour."""

from __future__ import annotations

import dataclasses as dc
import math
import typing as typ

import pytest

from cmd_mox.ipc import Invocation, PassthroughResult
from cmd_mox.passthrough import PassthroughConfig, PassthroughCoordinator

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    from cmd_mox.test_doubles import CommandDouble


@dc.dataclass(frozen=True)
class _FakeExpectation:
    """Carry the environment mapping of a fake double's expectation."""

    env: dict[str, str]


class _FakeDouble:
    """Minimal double exposing an expectation with a fixed environment."""

    def __init__(self, env: dict[str, str]) -> None:
        """Attach an expectation built from *env*."""
        self.expectation = _FakeExpectation(env)


def _make_invocation(command: str = "echo") -> Invocation:
    """Build an invocation for *command* with fixed arguments and empty stdin."""
    return Invocation(command=command, args=["hi"], stdin="", env={})


def test_prepare_request_registers_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    """Coordinator should record invocations until results arrive."""
    coordinator = PassthroughCoordinator()
    double = typ.cast("CommandDouble", _FakeDouble({"PATH": "/usr/bin"}))
    invocation = _make_invocation()

    config = PassthroughConfig(lookup_path="/usr/bin", timeout=2.0)
    response = coordinator.prepare_request(double, invocation, config)

    assert response.passthrough is not None
    assert response.passthrough.lookup_path == "/usr/bin"
    assert math.isclose(response.passthrough.timeout, 2.0)
    assert response.passthrough.extra_env == {"PATH": "/usr/bin"}
    assert response.env == {"PATH": "/usr/bin"}


def _unrecorded_commands(
    coordinator: PassthroughCoordinator, double: CommandDouble
) -> list[str]:
    """Return the commands of *double*'s unrecorded invocations."""
    return [
        invocation.command
        for pending_double, invocation in coordinator.unrecorded_invocations()
        if pending_double is double
    ]


def test_unrecorded_invocations_are_scoped_to_the_double() -> None:
    """Only the requested double's unrecorded invocations are returned."""
    coordinator = PassthroughCoordinator()
    first = typ.cast("CommandDouble", _FakeDouble({}))
    second = typ.cast("CommandDouble", _FakeDouble({}))
    config = PassthroughConfig(lookup_path="/bin", timeout=1.0)

    first_response = coordinator.prepare_request(first, _make_invocation("one"), config)
    coordinator.prepare_request(second, _make_invocation("two"), config)

    assert _unrecorded_commands(coordinator, first) == ["one"]

    directive = first_response.passthrough
    assert directive is not None
    coordinator.finalize_result(
        PassthroughResult(
            invocation_id=directive.invocation_id,
            stdout="",
            stderr="",
            exit_code=0,
        )
    )

    # Finalisation alone does not remove the call: the controller records it
    # afterwards, and verification must still see it until then.
    assert _unrecorded_commands(coordinator, first) == ["one"]

    coordinator.mark_recorded(directive.invocation_id)
    assert _unrecorded_commands(coordinator, first) == []


def test_expired_invocation_stays_unrecorded_after_pruning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pruning a pending entry leaves the expired call countable."""
    now = 100.0

    def fake_monotonic() -> float:
        """Return the test's current monotonic time."""
        return now

    monkeypatch.setattr("cmd_mox.passthrough.time.monotonic", fake_monotonic)
    coordinator = PassthroughCoordinator(cleanup_ttl=5.0)
    double = typ.cast("CommandDouble", _FakeDouble({}))
    config = PassthroughConfig(lookup_path="/bin", timeout=1.0)
    response = coordinator.prepare_request(double, _make_invocation("slow"), config)
    directive = response.passthrough
    assert directive is not None

    now += 10.0
    # A normal coordinator access prunes the expired pending entry, and the
    # late result is rejected. The call must still count as unrecorded.
    with pytest.raises(RuntimeError, match="Unexpected passthrough result"):
        coordinator.finalize_result(
            PassthroughResult(
                invocation_id=directive.invocation_id,
                stdout="",
                stderr="",
                exit_code=0,
            )
        )

    assert _unrecorded_commands(coordinator, double) == ["slow"]


def test_reset_clears_pending_and_unrecorded() -> None:
    """A new replay starts with no pending or unrecorded invocations."""
    coordinator = PassthroughCoordinator()
    double = typ.cast("CommandDouble", _FakeDouble({}))
    config = PassthroughConfig(lookup_path="/bin", timeout=1.0)
    response = coordinator.prepare_request(double, _make_invocation("one"), config)
    directive = response.passthrough
    assert directive is not None

    coordinator.reset()

    assert coordinator.unrecorded_invocations() == []
    with pytest.raises(RuntimeError, match="Unexpected passthrough result"):
        coordinator.finalize_result(
            PassthroughResult(
                invocation_id=directive.invocation_id,
                stdout="",
                stderr="",
                exit_code=0,
            )
        )


@pytest.mark.parametrize(
    ("initial_env", "extra_env", "expected_merged"),
    [
        pytest.param(
            {"ALPHA": "1"},
            {"BETA": "2"},
            {"ALPHA": "1", "BETA": "2"},
            id="merges_extra_env",
        ),
        pytest.param(
            {"SHARED": "expected"},
            {"SHARED": "override"},
            {"SHARED": "override"},
            id="extra_env_overrides_keys",
        ),
    ],
)
def test_prepare_request_extra_env_behavior(
    initial_env: dict[str, str],
    extra_env: dict[str, str],
    expected_merged: dict[str, str],
) -> None:
    """Extra env should extend and override expectation env as needed."""
    coordinator = PassthroughCoordinator()
    double = typ.cast("CommandDouble", _FakeDouble(initial_env))
    invocation = _make_invocation()

    config = PassthroughConfig(
        lookup_path="/bin",
        timeout=1.0,
        extra_env=extra_env,
    )
    response = coordinator.prepare_request(double, invocation, config)

    assert response.passthrough is not None
    assert response.passthrough.extra_env == expected_merged
    assert response.env == expected_merged


def test_finalize_result_returns_response_and_clears() -> None:
    """Finalisation should return stored data and clear pending state."""
    coordinator = PassthroughCoordinator()
    double = typ.cast("CommandDouble", _FakeDouble({"EXTRA": "1"}))
    invocation = _make_invocation("tool")
    invocation.env.update({"EXTRA": "1"})
    config = PassthroughConfig(lookup_path="/opt/bin", timeout=1.0)
    response = coordinator.prepare_request(double, invocation, config)
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
        """Return the test's current monotonic time."""
        return now

    monkeypatch.setattr("cmd_mox.passthrough.time.monotonic", fake_monotonic)

    coordinator = PassthroughCoordinator(cleanup_ttl=5.0)
    double = typ.cast("CommandDouble", _FakeDouble({}))
    invocation = _make_invocation("slow")
    config = PassthroughConfig(lookup_path="/bin", timeout=1.0)
    directive = coordinator.prepare_request(double, invocation, config)
    assert directive.passthrough is not None
    pending_id = directive.passthrough.invocation_id

    now += 10.0  # advance beyond cleanup TTL
    with pytest.raises(RuntimeError, match="Unexpected passthrough result"):
        coordinator.finalize_result(
            PassthroughResult(
                invocation_id=pending_id,
                stdout="",
                stderr="",
                exit_code=0,
            )
        )
