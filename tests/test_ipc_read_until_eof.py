"""Tests for the shared bounded EOF reader used by both IPC transports."""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox.ipc._limits import MessageTooLargeError
from cmd_mox.ipc.socket_utils import (
    DEFAULT_READ_CHUNK_SIZE,
    ReadOptions,
    read_until_eof,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import socket


class _ScriptedStream:
    """Socket double exposing a byte stream sliced by each receive size."""

    def __init__(
        self,
        payload: bytes = b"",
        *,
        recv_error: BaseException | None = None,
    ) -> None:
        """Store the streamed payload and any failure raised after it drains."""
        self._pending = bytearray(payload)
        self._recv_error = recv_error
        self.recv_sizes: list[int] = []
        self.timeouts: list[float] = []

    def settimeout(self, timeout: float) -> None:
        """Record the timeout, rejecting a value a real socket would refuse."""
        if timeout < 0:
            msg = "timeout must be non-negative"
            raise ValueError(msg)
        self.timeouts.append(timeout)

    def recv(self, size: int) -> bytes:
        """Return at most *size* buffered bytes, then the scripted failure."""
        self.recv_sizes.append(size)
        if not self._pending:
            if self._recv_error is not None:
                raise self._recv_error
            return b""
        chunk = bytes(self._pending[:size])
        del self._pending[:size]
        return chunk


class _DecreasingBudget:
    """Remaining-time double reporting a different budget on each call.

    A constant budget cannot tell a reader that recomputes the remaining time
    before every receive from one that resolves it once and reapplies it, so
    the budgets shrink and every deadline handed back is recorded.
    """

    def __init__(self, budgets: cabc.Iterable[float]) -> None:
        """Record the budgets to report, in order."""
        self._budgets = iter(budgets)
        self.deadlines: list[float] = []

    def __call__(self, deadline: float) -> float:
        """Record *deadline* and report the next budget."""
        self.deadlines.append(deadline)
        return next(self._budgets)


def _reader(
    sock: _ScriptedStream,
    *,
    max_bytes: int,
    chunk_size: int = DEFAULT_READ_CHUNK_SIZE,
    remaining: cabc.Callable[[float], float] | None = None,
) -> bytes:
    """Read *sock* with the supplied bounds and a fixed default budget."""
    return read_until_eof(
        typ.cast("socket.socket", sock),
        10.0,
        ReadOptions(
            max_bytes=max_bytes,
            chunk_size=chunk_size,
            remaining_time=remaining or (lambda _deadline: 2.5),
        ),
    )


def test_read_until_eof_joins_the_stream_and_draws_down_the_budget() -> None:
    """Every receive uses the freshly computed remaining budget."""
    sock = _ScriptedStream(b"abcdef")
    budget = _DecreasingBudget([2.5, 1.5, 0.5])

    payload = _reader(sock, max_bytes=10, chunk_size=4, remaining=budget)

    assert payload == b"abcdef", "the stream was not reassembled"
    # The request at each step is capped by the remaining budget plus the
    # one-byte EOF probe allowance.
    assert sock.recv_sizes == [4, 4, 4], "the receive size was not bounded"
    assert sock.timeouts == [2.5, 1.5, 0.5], "the budget was resolved only once"
    assert budget.deadlines == [10.0, 10.0, 10.0], "the deadline was renewed"


def test_read_until_eof_accepts_a_payload_exactly_at_the_limit() -> None:
    """A payload of exactly *max_bytes* followed by EOF succeeds."""
    sock = _ScriptedStream(b"abcd")

    payload = _reader(sock, max_bytes=4)

    assert payload == b"abcd", "a payload at the limit was refused"
    assert sock.recv_sizes == [5, 1], "the limit did not trigger an EOF probe"


def test_read_until_eof_rejects_the_first_byte_over_the_limit() -> None:
    """One byte past the limit is refused without retaining the payload."""
    sock = _ScriptedStream(b"abcde")

    with pytest.raises(MessageTooLargeError) as excinfo:
        _reader(sock, max_bytes=4, chunk_size=5)

    assert excinfo.value.received == 5, "the overflow byte was not counted"
    assert excinfo.value.limit == 4, "the configured limit was not reported"
    assert "abcde" not in str(excinfo.value), "payload content leaked into the error"
    assert sock.recv_sizes == [5], "the overflow was not detected in one receive"


def test_read_until_eof_shrinks_the_request_to_the_budget_plus_one() -> None:
    """A budget smaller than the chunk size caps each receive at the budget."""
    sock = _ScriptedStream(b"abc")

    payload = _reader(sock, max_bytes=3, chunk_size=8)

    assert payload == b"abc", "the payload at the limit was not returned"
    assert sock.recv_sizes == [4, 1], "the budget did not cap each receive"


def test_read_until_eof_propagates_the_deadline_timeout() -> None:
    """A deadline failure reaches the caller unchanged."""
    sock = _ScriptedStream(b"partial")
    failure = TimeoutError("IPC client operation timed out")

    def expire(_deadline: float) -> float:
        """Return one budget then fail, as an elapsed deadline does."""
        if not sock.recv_sizes:
            return 1.5
        raise failure

    with pytest.raises(TimeoutError) as excinfo:
        _reader(sock, max_bytes=10, remaining=expire)

    assert excinfo.value is failure, "the deadline failure was replaced"
    assert sock.timeouts == [1.5], "the applied timeout did not come from the budget"


def test_read_until_eof_propagates_a_receive_failure() -> None:
    """A socket error after partial data is not swallowed into a short payload."""
    failure = ConnectionResetError("peer vanished")
    sock = _ScriptedStream(b"partial", recv_error=failure)

    with pytest.raises(ConnectionResetError) as excinfo:
        _reader(sock, max_bytes=10)

    assert excinfo.value is failure, "the receive failure was replaced"
    assert sock.recv_sizes == [11, 4], "the failure skipped a completed read"


@pytest.mark.parametrize(
    ("max_bytes", "chunk_size"),
    [
        pytest.param(0, 4, id="zero-max-bytes"),
        pytest.param(-1, 4, id="negative-max-bytes"),
        pytest.param(4, 0, id="zero-chunk-size"),
        pytest.param(4, -1, id="negative-chunk-size"),
    ],
)
def test_read_until_eof_rejects_non_positive_bounds(
    max_bytes: int, chunk_size: int
) -> None:
    """A bound that cannot describe a usable read is rejected up front."""
    with pytest.raises(ValueError, match="must be positive"):
        _reader(_ScriptedStream(b""), max_bytes=max_bytes, chunk_size=chunk_size)
