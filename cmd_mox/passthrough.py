"""Passthrough request coordination for spy doubles."""

from __future__ import annotations

import dataclasses as dc
import threading
import time
import typing as typ
import uuid

from .ipc import Invocation, PassthroughRequest, PassthroughResult, Response

if typ.TYPE_CHECKING:
    from .test_doubles import CommandDouble


@dc.dataclass(slots=True)
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
    the per-request timeout would suggest. Stale pending entries are pruned
    lazily on each coordinator access.

    The unrecorded registry is separate from the pending map. An invocation
    stays registered until the controller records its result, or until
    :meth:`reset` starts a new replay, so verification can count a call that
    is in flight or whose pending entry expired.
    """

    def __init__(self, *, cleanup_ttl: float = 300.0) -> None:
        """Create an empty pending map guarded by a lock, with the cleanup TTL."""
        self._pending: dict[str, tuple[CommandDouble, Invocation, float]] = {}
        # Every invocation registered since the last reset whose result the
        # controller has not yet recorded. Verification reads this registry so
        # a call cannot slip between the pending map and the completed-call
        # log. Expired entries stay here: the call still happened.
        self._unrecorded: dict[str, tuple[CommandDouble, Invocation]] = {}
        self._lock = threading.Lock()
        self._cleanup_ttl = cleanup_ttl

    def _expiry_deadline(self, timeout: float) -> float:
        """Return the monotonic deadline for a passthrough invocation.

        Parameters
        ----------
        timeout : float
            Per-invocation timeout in seconds.

        Returns
        -------
        float
            The monotonic-clock deadline after the configured cleanup TTL.
        """
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
        """Record passthrough intent and return instructions for shim.

        Parameters
        ----------
        double : CommandDouble
            Spy double that owns the passthrough request.
        invocation : Invocation
            Invocation to retain until the shim reports its result.
        config : PassthroughConfig
            Lookup path, timeout, and environment additions for the request.

        Returns
        -------
        Response
            IPC response containing the passthrough instructions.
        """
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
            self._unrecorded[invocation_id] = (double, stored_invocation)

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

    def unrecorded_invocations(self) -> list[tuple[CommandDouble, Invocation]]:
        """Return registered invocations the controller has not recorded yet.

        Verification runs before the IPC server is stopped, so a passthrough
        call can still be in flight, or can have expired, when counts are
        checked. Those calls have already happened from the caller's
        perspective, so they must count towards an explicit expectation even
        though their result has not been recorded.

        Callers must snapshot this registry *before* the controller's
        completed-call log. A call leaves the registry only after it is
        appended to that log, so the two snapshots together contain every
        call; a call that completes between them appears in both and must be
        de-duplicated by invocation ID.

        Returns
        -------
        list[tuple[CommandDouble, Invocation]]
            Registered invocations paired with their owning double.
        """
        with self._lock:
            return list(self._unrecorded.values())

    def mark_recorded(self, invocation_id: str | None) -> None:
        """Stop counting *invocation_id* as unrecorded.

        Parameters
        ----------
        invocation_id : str or None
            Identifier of the invocation the controller has recorded. A
            ``None`` identifier belongs to a non-passthrough call and is
            ignored.
        """
        if invocation_id is None:
            return
        with self._lock:
            self._unrecorded.pop(invocation_id, None)

    def reset(self) -> None:
        """Forget pending and unrecorded invocations for a new replay."""
        with self._lock:
            self._pending.clear()
            self._unrecorded.clear()

    def finalize_result(
        self, result: PassthroughResult
    ) -> tuple[CommandDouble, Invocation, Response]:
        """Finalize passthrough and return its double, invocation, and response.

        The invocation remains in the unrecorded registry until the controller
        calls :meth:`mark_recorded` after appending it to its completed-call
        log.

        Parameters
        ----------
        result : PassthroughResult
            Result reported by the real command process.

        Returns
        -------
        tuple[CommandDouble, Invocation, Response]
            The owning double, updated invocation, and response delivered to it.

        Raises
        ------
        RuntimeError
            If the result does not correspond to a pending request.
        """
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

        recording_session = getattr(double, "_recording_session", None)
        if recording_session is not None:
            recording_session.record(invocation, resp)

        return double, invocation, resp
