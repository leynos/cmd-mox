"""Passthrough request coordination for spy doubles."""

from __future__ import annotations

import dataclasses as dc
import threading
import time
import typing as t
import uuid

from .ipc import Invocation, PassthroughRequest, PassthroughResult, Response

if t.TYPE_CHECKING:
    from .test_doubles import CommandDouble


@dc.dataclass
class PassthroughConfig:
    """Configuration for passthrough request preparation."""

    lookup_path: str
    timeout: float
    extra_env: dict[str, str] | None = None


class PassthroughCoordinator:
    """Manages pending passthrough requests and result finalization.

    Concurrency
    -----------
    All public methods are thread-safe. Internal state is protected by a
    lock, allowing concurrent prepare/finalize calls from multiple threads.

    Lifetime Guarantees
    -------------------
    Pending entries are assigned a monotonic deadline of
    ``max(timeout, cleanup_ttl)`` from creation time. The ``timeout`` value
    originates from :attr:`~cmd_mox.command_runner.CommandRunner.timeout`,
    while ``cleanup_ttl`` is set via this class's constructor. Because
    ``cleanup_ttl`` may exceed ``timeout``, entries can persist longer than
    the per-request timeout would suggest. Stale entries are pruned lazily
    on each coordinator access.
    """

    def __init__(self, *, cleanup_ttl: float = 300.0) -> None:
        self._pending: dict[str, tuple[CommandDouble, Invocation, float]] = {}
        self._lock = threading.Lock()
        self._cleanup_ttl = cleanup_ttl

    def _expiry_deadline(self, timeout: float) -> float:
        """Return the monotonic deadline for a passthrough invocation."""
        ttl = max(timeout, self._cleanup_ttl)
        return time.monotonic() + ttl

    def _prune_expired_locked(self, *, now: float | None = None) -> None:
        """Remove stale passthrough requests while holding ``self._lock``."""
        current = time.monotonic() if now is None else now
        expired = [
            key
            for key, (_, _, deadline) in self._pending.items()
            if deadline <= current
        ]
        for key in expired:
            self._pending.pop(key, None)

    def prepare_request(
        self,
        double: CommandDouble,
        invocation: Invocation,
        config: PassthroughConfig,
    ) -> Response:
        """Record passthrough intent and return instructions for shim."""
        invocation_id = invocation.invocation_id or uuid.uuid4().hex
        invocation.invocation_id = invocation_id

        stored_invocation = Invocation(
            command=invocation.command,
            args=list(invocation.args),
            stdin=invocation.stdin,
            env=dict(invocation.env),
            stdout="",
            stderr="",
            exit_code=0,
            invocation_id=invocation_id,
        )

        with self._lock:
            self._prune_expired_locked()
            self._pending[invocation_id] = (
                double,
                stored_invocation,
                self._expiry_deadline(config.timeout),
            )

        env = dict(double.expectation.env)
        if config.extra_env:
            env.update(config.extra_env)
        passthrough = PassthroughRequest(
            invocation_id=invocation_id,
            lookup_path=config.lookup_path,
            extra_env=dict(env),
            timeout=config.timeout,
        )
        return Response(env=dict(env), passthrough=passthrough)

    def finalize_result(
        self, result: PassthroughResult
    ) -> tuple[CommandDouble, Invocation, Response]:
        """Finalize passthrough and return (double, invocation, response)."""
        with self._lock:
            self._prune_expired_locked()
            entry = self._pending.pop(result.invocation_id, None)

        if entry is None:
            msg = f"Unexpected passthrough result for {result.invocation_id}"
            raise RuntimeError(msg)

        double, invocation, _ = entry
        resp = Response(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            env=dict(invocation.env),
        )
        invocation.apply(resp)
        return double, invocation, resp

    def has_pending(self, invocation_id: str) -> bool:
        """Return ``True`` if *invocation_id* is awaiting passthrough results."""
        with self._lock:
            self._prune_expired_locked()
            return invocation_id in self._pending

    def pending_count(self) -> int:
        """Return the number of outstanding passthrough invocations."""
        with self._lock:
            self._prune_expired_locked()
            return len(self._pending)
