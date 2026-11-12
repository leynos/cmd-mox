"""Unit tests for the IPC client's request, transport, and wire helpers."""

from __future__ import annotations

import json
import os
import pathlib
import socket
import typing as typ

import pytest

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import _client_events, _observability, wire
from cmd_mox.ipc import client as ipc_client
from cmd_mox.ipc.client import RetryConfig, _ConnectionContext
from cmd_mox.ipc.models import Response

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def _context(
    *, deadline: float | None = None, timeout: float = 1.0
) -> _ConnectionContext:
    """Build a valid connection context for transport tests."""
    return _ConnectionContext(
        timeout=timeout,
        retry_config=RetryConfig(retries=1, backoff=0.0, jitter=0.0),
        correlation_id="corr-1",
        deadline=deadline,
    )


class _RecordingSocket:
    """Socket double recording phase calls and returning scripted chunks."""

    def __init__(
        self,
        chunks: cabc.Sequence[bytes] = (),
        *,
        connect_error: BaseException | None = None,
        recv_error: BaseException | None = None,
    ) -> None:
        """Store the scripted chunks and failures."""
        self.chunks = list(chunks)
        self.connect_error = connect_error
        self.recv_error = recv_error
        self.timeouts: list[float] = []
        self.sent: list[bytes] = []
        self.shutdowns: list[int] = []
        self.recv_sizes: list[int] = []
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        """Record the timeout assigned to the socket."""
        self.timeouts += [timeout]

    def connect(self, _address: str) -> None:
        """Fail if scripted, otherwise accept the connection."""
        if self.connect_error is not None:
            raise self.connect_error

    def sendall(self, payload: bytes) -> None:
        """Record the payload sent to the peer."""
        self.sent += [payload]

    def shutdown(self, how: int) -> None:
        """Record the shutdown mode."""
        self.shutdowns += [how]

    def recv(self, size: int) -> bytes:
        """Return the next scripted chunk, or raise a scripted failure."""
        self.recv_sizes.append(size)
        if self.recv_error is not None:
            raise self.recv_error
        return self.chunks.pop(0) if self.chunks else b""

    def close(self) -> None:
        """Mark the socket closed."""
        self.closed = True

    def __enter__(self) -> _RecordingSocket:
        """Return this socket for use as a context manager."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the socket when the context manager exits."""
        self.close()


def test_get_validated_socket_path_reports_the_exact_missing_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset environment variable names itself in the raised error."""
    monkeypatch.delenv(CMOX_IPC_SOCKET_ENV, raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        ipc_client._get_validated_socket_path()

    assert str(excinfo.value) == f"{CMOX_IPC_SOCKET_ENV} is not set", (
        "the missing-variable message changed"
    )


def test_get_validated_socket_path_converts_the_value_to_a_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured path is returned as a ``Path``."""
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, "nested/ipc.sock")

    assert ipc_client._get_validated_socket_path() == pathlib.Path("nested/ipc.sock"), (
        "the configured socket path was not converted"
    )


def test_decode_response_returns_the_parsed_response() -> None:
    """A valid payload becomes the domain response it encodes."""
    expected = Response(stdout="out", stderr="err", exit_code=3)
    raw = json.dumps(wire.response_to_payload(expected)).encode()

    decoded = ipc_client._decode_response(raw)

    assert decoded == expected, "the decoded response did not round-trip"


def test_decode_response_reports_the_exact_invalid_json_message() -> None:
    """Invalid JSON is rejected with the documented message."""
    with pytest.raises(RuntimeError) as excinfo:
        ipc_client._decode_response(b"not json")

    assert str(excinfo.value) == "Invalid JSON from IPC server", (
        "the invalid-JSON message changed"
    )


def test_read_all_requests_fixed_size_chunks_until_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chunks are requested at the documented size and concatenated."""
    sock = _RecordingSocket([b"first", b"second"])
    monkeypatch.setattr(ipc_client, "_remaining_time", lambda _deadline: 1.0)

    result = ipc_client._read_all(typ.cast("socket.socket", sock), 10.0)

    assert result == b"firstsecond", "the received chunks were not joined"
    assert sock.recv_sizes == [1024, 1024, 1024], "the read chunk size changed"
    assert sock.timeouts == [1.0, 1.0, 1.0], (
        "each receive must draw down the shared deadline"
    )


def test_read_all_returns_empty_bytes_at_immediate_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A peer that closes immediately yields an empty payload."""
    sock = _RecordingSocket()
    monkeypatch.setattr(ipc_client, "_remaining_time", lambda _deadline: 1.0)

    assert ipc_client._read_all(typ.cast("socket.socket", sock), 10.0) == b"", (
        "an immediate EOF did not yield empty bytes"
    )


def test_read_all_propagates_a_receive_failure_after_a_partial_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure after partial data is not swallowed into a short payload."""
    failure = OSError("peer vanished")
    sock = _RecordingSocket(recv_error=failure)
    monkeypatch.setattr(ipc_client, "_remaining_time", lambda _deadline: 1.0)

    with pytest.raises(OSError, match="peer vanished") as excinfo:
        ipc_client._read_all(typ.cast("socket.socket", sock), 10.0)

    assert excinfo.value is failure, "the receive failure was replaced"


@pytest.mark.parametrize(
    ("context_deadline", "explicit_deadline", "expected"),
    [
        pytest.param(100.0, 50.0, 50.0, id="explicit-wins"),
        pytest.param(100.0, None, 100.0, id="context-used"),
        pytest.param(None, None, 77.0, id="computed"),
    ],
)
def test_connect_unix_with_retries_selects_the_documented_deadline(
    monkeypatch: pytest.MonkeyPatch,
    context_deadline: float | None,
    explicit_deadline: float | None,
    expected: float,
) -> None:
    """Explicit, contextual, and computed deadlines are ordered as documented."""
    sock = _RecordingSocket()
    observed: list[float] = []

    def remaining(deadline: float) -> float:
        """Record the deadline every phase consults."""
        observed.append(deadline)
        return 1.0

    monkeypatch.setattr(ipc_client, "_remaining_time", remaining)
    monkeypatch.setattr(ipc_client, "_compute_deadline", lambda _timeout: 77.0)
    monkeypatch.setattr(ipc_client.socket, "socket", lambda *_args: sock)
    context = _context(deadline=context_deadline)

    result = ipc_client._connect_unix_with_retries(
        pathlib.Path("ipc.sock"), context, deadline=explicit_deadline
    )

    assert result is sock, "the connected socket was not returned"
    assert observed, "the connect phases never consulted a deadline"
    assert set(observed) == {expected}, (
        "the connect phases did not share the selected deadline"
    )


def test_connect_unix_with_retries_opens_an_af_unix_stream_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The socket is created with the Unix stream family and type."""
    created: list[tuple[object, ...]] = []
    sock = _RecordingSocket()

    def factory(*args: object) -> _RecordingSocket:
        """Record the socket constructor arguments."""
        created.append(args)
        return sock

    monkeypatch.setattr(ipc_client, "_remaining_time", lambda _deadline: 1.0)
    monkeypatch.setattr(ipc_client.socket, "socket", factory)

    ipc_client._connect_unix_with_retries(
        pathlib.Path("ipc.sock"), _context(deadline=10.0)
    )

    assert created == [(socket.AF_UNIX, socket.SOCK_STREAM)], (
        "the Unix socket family or type changed"
    )


def test_connect_unix_with_retries_closes_a_failed_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused connect releases the socket before the error escapes."""
    failure = ConnectionRefusedError("refused")
    sock = _RecordingSocket(connect_error=failure)
    monkeypatch.setattr(ipc_client, "_remaining_time", lambda _deadline: 1.0)
    monkeypatch.setattr(ipc_client.socket, "socket", lambda *_args: sock)

    with pytest.raises(ConnectionRefusedError) as excinfo:
        ipc_client._connect_unix_with_retries(
            pathlib.Path("ipc.sock"), _context(deadline=10.0)
        )

    assert excinfo.value is failure, "the connect failure was replaced"
    assert sock.closed is True, "the failed socket was not closed"


def test_send_unix_request_forwards_the_shared_deadline_and_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One deadline bounds connecting, sending, shutdown, and reading."""
    sock = _RecordingSocket([b"response"])
    connects: list[tuple[pathlib.Path, _ConnectionContext, float | None]] = []

    def connect(
        sock_path: pathlib.Path,
        context: _ConnectionContext,
        *,
        deadline: float | None = None,
    ) -> _RecordingSocket:
        """Record the forwarded connect arguments."""
        connects.append((sock_path, context, deadline))
        return sock

    monkeypatch.setattr(ipc_client, "_connect_unix_with_retries", connect)
    monkeypatch.setattr(ipc_client, "_remaining_time", lambda _deadline: 2.5)
    context = _context(deadline=42.0)

    result = ipc_client._send_unix_request(
        pathlib.Path("ipc.sock"), b"payload", context
    )

    assert result == b"response", "the reply bytes were not returned"
    assert connects == [(pathlib.Path("ipc.sock"), context, 42.0)], (
        "the shared deadline was not forwarded to the connect retries"
    )
    assert sock.timeouts == [2.5, 2.5, 2.5, 2.5], (
        "send, shutdown, and each read did not share the request deadline"
    )
    assert sock.sent == [b"payload"], "the payload was not sent verbatim"
    assert sock.shutdowns == [socket.SHUT_WR], "the write side was not shut down"
    assert sock.closed is True, "the socket was not released"


def test_send_unix_request_computes_a_deadline_when_none_is_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing context deadline is computed once and shared."""
    sock = _RecordingSocket([b"ok"])
    deadlines: list[float | None] = []

    def connect(
        _sock_path: pathlib.Path,
        _context: _ConnectionContext,
        *,
        deadline: float | None = None,
    ) -> _RecordingSocket:
        """Record the computed deadline."""
        deadlines.append(deadline)
        return sock

    monkeypatch.setattr(ipc_client, "_connect_unix_with_retries", connect)
    monkeypatch.setattr(ipc_client, "_compute_deadline", lambda _timeout: 88.0)
    monkeypatch.setattr(ipc_client, "_remaining_time", lambda _deadline: 1.0)

    ipc_client._send_unix_request(
        pathlib.Path("ipc.sock"), b"payload", _context(deadline=None)
    )

    assert deadlines == [88.0], "the computed deadline was not forwarded"


def test_build_request_envelope_overwrites_fields_without_mutating_input() -> None:
    """Envelope fields win over caller data, which is left untouched."""
    data = {
        "kind": "spoofed",
        "correlation_id": "spoofed",
        "invocation_id": "abc",
        "command": "café",
    }

    payload, correlation_id = ipc_client._build_request_envelope("invocation", data)

    expected = {
        "kind": "invocation",
        "correlation_id": "abc",
        "invocation_id": "abc",
        "command": "café",
    }
    assert json.loads(payload.decode("utf-8")) == expected, (
        "the envelope did not carry the reserved fields and body correctly"
    )
    assert correlation_id == "abc", "the correlation identifier was not returned"
    assert data["kind"] == "spoofed", "the caller's mapping was mutated"
    assert data["correlation_id"] == "spoofed", "the caller's identifier was mutated"


def test_dispatch_request_validates_before_looking_up_the_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An invalid context fails before any environment or transport work."""
    looked_up: list[str] = []
    monkeypatch.setattr(
        ipc_client,
        "_get_validated_socket_path",
        lambda: looked_up.append("looked up"),
    )

    with pytest.raises(ValueError, match="timeout must be > 0 and finite"):
        ipc_client._dispatch_request(b"payload", _context(timeout=0.0))

    assert not looked_up, "the socket path was read before validation"


@pytest.mark.parametrize("transport", ["unix", "windows"])
def test_dispatch_request_sends_over_the_host_transport(
    monkeypatch: pytest.MonkeyPatch, transport: str
) -> None:
    """Each host transport receives the payload, path, and context verbatim."""
    calls: list[tuple[str, pathlib.Path, bytes, _ConnectionContext]] = []
    monkeypatch.setattr(
        ipc_client, "_get_validated_socket_path", lambda: pathlib.Path("ipc")
    )
    monkeypatch.setattr(ipc_client.path_utils, "IS_WINDOWS", transport == "windows")

    def record(
        name: str,
    ) -> cabc.Callable[[pathlib.Path, bytes, _ConnectionContext], bytes]:
        """Build a recorder for one transport helper."""

        def send(
            sock_path: pathlib.Path, payload: bytes, context: _ConnectionContext
        ) -> bytes:
            """Record the transport invocation."""
            calls.append((name, sock_path, payload, context))
            return b"reply"

        return send

    monkeypatch.setattr(ipc_client, "_send_pipe_request", record("pipe"))
    monkeypatch.setattr(ipc_client, "_send_unix_request", record("unix"))
    context = _context(deadline=5.0)

    result = ipc_client._dispatch_request(b"payload", context)

    expected = "pipe" if transport == "windows" else "unix"
    assert result == b"reply", "the transport reply was not returned"
    assert calls == [(expected, pathlib.Path("ipc"), b"payload", context)], (
        "the wrong transport or arguments were used"
    )


def test_perform_request_emits_start_then_success_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The started event precedes success and carries the request details."""
    response = Response(stdout="out")

    def dispatch(payload: bytes, _context: _ConnectionContext) -> bytes:
        """Assert the payload is forwarded verbatim and return a reply."""
        assert payload == b"payload", "the payload was not forwarded"
        return json.dumps(wire.response_to_payload(response)).encode()

    monkeypatch.setattr(ipc_client, "_dispatch_request", dispatch)

    with _observability.capture_events() as captured:
        result = ipc_client._perform_request(b"payload", "invocation", _context())

    events = list(captured)
    assert result == response, "the decoded response was not returned"
    assert [event.outcome for event in events] == ["started", "success"], (
        "the request lifecycle events were misordered or missing"
    )
    started, succeeded = events
    assert started.kind == "invocation", "the kind was not carried to the event"
    assert started.message_size == len(b"payload"), "the message size was not recorded"
    assert started.correlation_id == "corr-1", "the correlation id was dropped"
    assert succeeded.kind == "invocation", "the success event lost the kind"
    assert succeeded.correlation_id == "corr-1", "success lost the correlation id"
    assert succeeded.duration_ms is not None, "success lost its duration"
    assert succeeded.duration_ms >= 0, "success reported a negative duration"


def test_perform_request_emits_an_error_event_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dispatch failure is categorised, emitted, and re-raised verbatim."""
    failure = TimeoutError("late")

    def dispatch(_payload: bytes, _context: _ConnectionContext) -> bytes:
        """Raise the sentinel failure."""
        raise failure

    monkeypatch.setattr(ipc_client, "_dispatch_request", dispatch)

    with (
        _observability.capture_events() as events,
        pytest.raises(TimeoutError) as excinfo,
    ):
        ipc_client._perform_request(b"payload", "invocation", _context())

    assert excinfo.value is failure, "the dispatch failure was replaced"
    started, failed = events
    assert [event.outcome for event in events] == ["started", "error"], (
        "the error event was missing or misordered"
    )
    assert started.kind == "invocation", "the kind was lost on failure"
    assert failed.error_category == "TimeoutError", "the error category changed"
    assert failed.duration_ms is not None, "the error lost its duration"


def test_dispatch_request_uses_the_environment_socket_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validated environment path reaches the host transport."""
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, os.fspath(pathlib.Path("ipc.sock")))
    monkeypatch.setattr(ipc_client.path_utils, "IS_WINDOWS", False)
    sent: list[tuple[pathlib.Path, bytes, _ConnectionContext]] = []

    def send(
        sock_path: pathlib.Path, payload: bytes, context: _ConnectionContext
    ) -> bytes:
        """Record the validated path."""
        sent.append((sock_path, payload, context))
        return b"reply"

    monkeypatch.setattr(ipc_client, "_send_unix_request", send)
    context = _context()

    ipc_client._dispatch_request(b"payload", context)

    assert sent == [(pathlib.Path("ipc.sock"), b"payload", context)], (
        "the environment socket path was not forwarded"
    )


def test_perform_request_uses_the_client_event_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lifecycle events use the shared client request operation name."""
    monkeypatch.setattr(
        ipc_client, "_dispatch_request", lambda _payload, _context: b"not json"
    )

    with (
        _observability.capture_events() as events,
        pytest.raises(RuntimeError),
    ):
        ipc_client._perform_request(b"payload", "invocation", _context())

    assert {event.operation for event in events} == {
        _client_events.REQUEST_OPERATION
    }, "the request events used the wrong operation"
