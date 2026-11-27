"""Bounded reply delivery for the named-pipe server.

On a server-side pipe handle ``FlushFileBuffers`` waits until the client has
read the buffered data, but it takes no timeout and cannot be cancelled, so a
client that never reads would block the handler and server shutdown
indefinitely. The transport therefore writes the reply with an overlapped
``WriteFile`` and then issues a bounded overlapped read, treating the peer's
close as the delivery acknowledgement. That read is what bounds a client which
reads slowly or stops reading: the same per-connection deadline applies, and
both operations are cancelled and drained in the serving thread before their
buffer and event are released.

A close acknowledges the exchange, not consumption. A reply that fits the pipe
buffer is completed by ``WriteFile`` before the client reads it, so a client
that closes without reading is still reported as delivered. That is the same
guarantee a successful Unix flush gives, and it is the strongest one the
protocol carries.
"""

from __future__ import annotations

import functools
import typing as typ

from ._deadline import TIMEOUT_MESSAGE, remaining, remaining_ms
from .windows import (
    ERROR_BROKEN_PIPE,
    ERROR_IO_PENDING,
    ERROR_MORE_DATA,
    ERROR_NO_DATA,
    NamedPipeBackend,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

type _WaitForEvent = cabc.Callable[[object, int], int]
type _CancelOverlapped = cabc.Callable[[object, object], None]

#: Bytes requested by the acknowledgement read. Any data at all is a protocol
#: violation, so a one-byte probe is enough; a larger message reports
#: ``ERROR_MORE_DATA``, which is also treated as unexpected data.
_PROBE_SIZE: typ.Final[int] = 1

#: Win32 statuses reporting that the client closed its end of the pipe.
_PEER_CLOSED: typ.Final[frozenset[int]] = frozenset({ERROR_BROKEN_PIPE, ERROR_NO_DATA})


class ReplyDelivery:
    """Write one reply and confirm that the client received it.

    Parameters
    ----------
    backend:
        The Win32 port the transport was built with.
    wait:
        Waits on an overlapped event and the shutdown event, returning the
        ``WaitForMultipleObjects`` result.
    cancel:
        Cancels a pending overlapped operation and drains it.

    Notes
    -----
    Confirmation is opportunistic within the connection budget: a client that
    keeps the pipe open past the deadline is reported as unconfirmed rather than
    treated as a failure, because the reply may still have been consumed.
    """

    def __init__(
        self,
        backend: NamedPipeBackend,
        *,
        wait: _WaitForEvent,
        cancel: _CancelOverlapped,
    ) -> None:
        """Store the port and the transport's wait and cancel primitives."""
        self._backend = backend
        self._wait = wait
        self._cancel = cancel

    def deliver(self, handle: object, payload: bytes, deadline: float) -> bool:
        """Write *payload*, then wait for the client to acknowledge it.

        Returns
        -------
        bool
            ``True`` when the client closed the pipe after the reply was
            queued, which is the only acknowledgement the protocol carries;
            ``False`` when it sent more data instead, leaving delivery
            unconfirmed.

        Notes
        -----
        Delivery is abandoned with ``TimeoutError`` when *deadline* expires
        before the write completes or before the client closes, or when the
        server stops first. The pending operation is cancelled and drained
        before that error propagates.
        """
        remaining(deadline)
        self._write(handle, payload, deadline)
        return self._confirm(handle, deadline)

    def _write(self, handle: object, payload: bytes, deadline: float) -> None:
        """Issue the overlapped write and wait for it within *deadline*."""
        self._run(
            handle,
            deadline,
            lambda overlapped: self._backend.write_file(handle, payload, overlapped),
        )

    def _confirm(self, handle: object, deadline: float) -> bool:
        """Wait for the client to close the pipe after the reply is queued.

        Returns
        -------
        bool
            ``True`` when the read reports the peer closed, ``False`` when the
            peer sent data instead.

        Notes
        -----
        A read that outlives *deadline*, or one interrupted by a server stop,
        is cancelled and drained before ``TimeoutError`` propagates.
        """
        remaining(deadline)
        # The probe buffer must outlive the operation and its cancellation
        # drain, so it is created here rather than inside the operation.
        buffer = self._backend.allocate_read_buffer(_PROBE_SIZE)
        try:
            self._run(
                handle,
                deadline,
                lambda overlapped: self._backend.read_file(handle, buffer, overlapped),
            )
        except self._backend.error as exc:
            winerror = getattr(exc, "winerror", None)
            if winerror in _PEER_CLOSED:
                return True
            if winerror == ERROR_MORE_DATA:
                # The client wrote again instead of closing, so its request
                # stream is still live and nothing confirms that it read the
                # reply.
                return False
            raise
        # A completed read means the client sent another message; a zero-byte
        # message is equally unexpected.
        return False

    def _run(
        self,
        handle: object,
        deadline: float,
        start: cabc.Callable[[object], tuple[int, object]],
    ) -> int:
        """Run one overlapped operation to completion under *deadline*.

        Parameters
        ----------
        handle:
            The pipe instance to issue the operation on.
        deadline:
            Absolute budget shared by the operation and its wait.
        start:
            Issues the operation against the overlapped structure this helper
            allocates and awaits.

        Returns
        -------
        int
            The bytes the completed operation transferred.
        """
        overlapped, event = self._backend.create_overlapped()
        try:
            if self._begin(functools.partial(start, overlapped)) == ERROR_IO_PENDING:
                self._await(handle, overlapped, event, deadline)
            return self._backend.get_overlapped_result(handle, overlapped)
        finally:
            self._backend.close_handle(event)

    def _begin(self, operation: cabc.Callable[[], tuple[int, object]]) -> int:
        """Issue *operation* and report whether it is still pending.

        pywin32 reports a still-pending overlapped operation either as a
        returned ``ERROR_IO_PENDING`` status or as a raised error carrying that
        code, so both shapes are normalised here.

        Returns
        -------
        int
            ``ERROR_IO_PENDING`` while in flight, otherwise the status the
            operation reported on completion.
        """
        try:
            status, _ = operation()
        except self._backend.error as exc:
            if getattr(exc, "winerror", None) != ERROR_IO_PENDING:
                raise
            return ERROR_IO_PENDING
        return status

    def _await(
        self, handle: object, overlapped: object, event: object, deadline: float
    ) -> None:
        """Wait for a pending operation, cancelling it on expiry or shutdown.

        Raises
        ------
        TimeoutError
            If the deadline expires or the server stops first. The operation
            is cancelled and drained before the error propagates, so the
            kernel no longer writes into the buffer it was given.

        Notes
        -----
        A wait that fails for another reason leaves the operation pending, and
        the caller releases the buffer as the error unwinds, so that path
        drains the operation too. The wait's own error is what propagates.
        """
        completed: int | None = None
        try:
            completed = self._wait(event, remaining_ms(deadline))
        finally:
            if completed != self._backend.WAIT_OBJECT_0:
                self._cancel(handle, overlapped)
        if completed == self._backend.WAIT_OBJECT_0:
            return
        raise TimeoutError(TIMEOUT_MESSAGE)


__all__ = ["ReplyDelivery"]
