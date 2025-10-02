"""Passthrough request coordination for spy doubles."""

from __future__ import annotations

import threading
import typing as t
import uuid

from .ipc import Invocation, PassthroughRequest, PassthroughResult, Response

if t.TYPE_CHECKING:
    from .test_doubles import CommandDouble


class PassthroughCoordinator:
    """Manages pending passthrough requests and result finalization."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[CommandDouble, Invocation]] = {}
        self._lock = threading.Lock()

    def prepare_request(
        self,
        double: CommandDouble,
        invocation: Invocation,
        lookup_path: str,
        timeout: float,
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
            self._pending[invocation_id] = (double, stored_invocation)

        env = double.expectation.env
        passthrough = PassthroughRequest(
            invocation_id=invocation_id,
            lookup_path=lookup_path,
            extra_env=dict(env),
            timeout=timeout,
        )
        return Response(env=dict(env), passthrough=passthrough)

    def finalize_result(
        self, result: PassthroughResult
    ) -> tuple[CommandDouble, Invocation, Response]:
        """Finalize passthrough and return (double, invocation, response)."""
        with self._lock:
            entry = self._pending.pop(result.invocation_id, None)

        if entry is None:
            msg = f"Unexpected passthrough result for {result.invocation_id}"
            raise RuntimeError(msg)

        double, invocation = entry
        resp = Response(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.exit_code,
            env=dict(double.expectation.env),
        )
        invocation.apply(resp)
        return double, invocation, resp
