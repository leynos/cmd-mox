"""Fail-fast rejection of mock calls that definitely cannot match.

The controller keeps a ledger of these rejections for the duration of one
replay. It is separate from the journal so that a bounded journal can evict
the call without hiding it from :meth:`CmdMox.verify`.
"""

from __future__ import annotations

import dataclasses as dc
import threading
import typing as typ

from ._argument_redaction import redact_sensitive_argument_values
from .ipc import Invocation, Response

if typ.TYPE_CHECKING:
    from .test_doubles import CommandDouble


@dc.dataclass(slots=True, frozen=True)
class RejectedMockCall:
    """A mock call rejected at invocation time by fail-fast mode."""

    double: CommandDouble
    invocation: Invocation
    reason: str


def rejection_response(invocation: Invocation, reason: str) -> Response:
    """Build the failure response for a definitely mismatched mock call.

    Returns
    -------
    Response
        Exit status ``1`` with a redacted diagnostic on stderr and no stdout.
    """
    safe_args = redact_sensitive_argument_values(
        invocation.args, command=invocation.command
    )
    call = " ".join([invocation.command, *safe_args])
    diagnostic = f"No mock expectation matches: {call} ({reason})\n"
    return Response(stdout="", stderr=diagnostic, exit_code=1)


class RejectionLedger:
    """Thread-safe record of the calls rejected during the current replay."""

    def __init__(self) -> None:
        """Create an empty ledger."""
        self._lock = threading.Lock()
        self._entries: list[RejectedMockCall] = []

    def clear(self) -> None:
        """Drop every recorded rejection, starting a fresh replay."""
        with self._lock:
            self._entries.clear()

    def record(
        self, double: CommandDouble, invocation: Invocation, reason: str
    ) -> None:
        """Remember one rejected call."""
        with self._lock:
            self._entries.append(RejectedMockCall(double, invocation, reason))

    def first(self) -> RejectedMockCall | None:
        """Return the earliest rejection, or ``None`` when there is none."""
        with self._lock:
            return self._entries[0] if self._entries else None
