"""Client-side observability vocabulary for the IPC transports.

This module owns the operation names, correlation-identifier derivation, and
event shapes used by :mod:`cmd_mox.ipc.client`. It depends only on the
transport-neutral seam in :mod:`cmd_mox.ipc._observability` and on the
transport-neutral request core in :mod:`cmd_mox.ipc._server_core`, neither of
which imports a transport, so it introduces no import cycle. Every field
emitted here is a bounded dimension; see the observability module's never-log
list.
"""

from __future__ import annotations

import dataclasses as dc
import logging
import typing as typ
import uuid

from cmd_mox import _path_utils as path_utils

from . import _observability
from ._server_core import bounded_correlation_id

if typ.TYPE_CHECKING:
    import collections.abc as cabc

REQUEST_OPERATION: typ.Final[str] = "ipc.client.request"
CONNECT_RETRY_OPERATION: typ.Final[str] = "ipc.client.connect_retry"

# Client events describe work performed by :mod:`cmd_mox.ipc.client`, so they
# are attributed to that module's logger rather than this helper module's.
_CLIENT_LOGGER: typ.Final[logging.Logger] = logging.getLogger("cmd_mox.ipc.client")


def client_transport() -> _observability.Transport:
    """Return the transport name this host uses for IPC.

    Returns
    -------
    str
        ``"named_pipe"`` on Windows hosts and ``"unix"`` elsewhere.
    """
    return "named_pipe" if path_utils.IS_WINDOWS else "unix"


def resolve_correlation_id(data: cabc.Mapping[str, typ.Any]) -> str:
    """Return the correlation identifier for a request envelope.

    The model's ``invocation_id`` is reused when the request already carries a
    usable one, so the client and server records share a single identifier. An
    absent, empty, over-long, or non-conforming value is treated as no
    identifier at all and a fresh opaque UUID is minted instead; identifiers are
    never derived from request content, never exceed
    :data:`~cmd_mox.ipc._server_core.MAX_CORRELATION_ID_LENGTH` characters, and
    are always drawn from the log-safe alphabet
    :func:`~cmd_mox.ipc._server_core.bounded_correlation_id` accepts.

    Parameters
    ----------
    data:
        The wire-format model fields for the request.

    Returns
    -------
    str
        The opaque, length-bounded correlation identifier for this request.
    """
    existing = bounded_correlation_id(data.get("invocation_id"))
    if existing is not None:
        return existing
    return uuid.uuid4().hex


@dc.dataclass(frozen=True, slots=True)
class RequestEvent:
    """One bounded client request-lifecycle event."""

    kind: str
    outcome: str
    correlation_id: str | None = None
    duration_ms: float | None = None
    message_size: int | None = None
    error_category: str | None = None

    def emit(self) -> None:
        """Emit this event through the shared observability seam."""
        _observability.emit(
            _observability.IPCEvent(
                operation=REQUEST_OPERATION,
                transport=client_transport(),
                kind=self.kind,
                outcome=self.outcome,
                error_category=self.error_category,
                duration_ms=self.duration_ms,
                message_size=self.message_size,
                correlation_id=self.correlation_id,
            ),
            logger=_CLIENT_LOGGER,
        )


def emit_connect_retry(
    transport: _observability.Transport,
    attempt: int,
    exc: Exception,
    correlation_id: str | None,
) -> None:
    """Emit a bounded event for one failed connection attempt.

    Parameters
    ----------
    transport:
        Transport whose connection attempt failed.
    attempt:
        The 0-based attempt index, reported 1-based to match the retry log.
    exc:
        The failure. Only its class name is recorded; never its message.
    correlation_id:
        The request's opaque correlation identifier, when known.
    """
    _observability.emit(
        _observability.IPCEvent(
            operation=CONNECT_RETRY_OPERATION,
            transport=transport,
            outcome="attempt_failed",
            error_category=type(exc).__name__,
            attempt=attempt + 1,
            correlation_id=correlation_id,
        ),
        logger=_CLIENT_LOGGER,
    )


__all__ = [
    "CONNECT_RETRY_OPERATION",
    "REQUEST_OPERATION",
    "RequestEvent",
    "client_transport",
    "emit_connect_retry",
    "resolve_correlation_id",
]
