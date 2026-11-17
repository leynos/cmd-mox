"""Bounded resource limits enforced by the Unix IPC server."""

from __future__ import annotations

import json
import queue
import socket
import socketserver
import sys
import threading
import time
import typing as typ
from functools import partial

import pytest

from cmd_mox.ipc import (
    Invocation,
    IPCHandlers,
    IPCServer,
    Response,
    _observability,
)
from cmd_mox.ipc import server as _ipc_server
from cmd_mox.ipc._limits import MAX_ACTIVE_CLIENTS, acquire_client_slot

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

pytestmark = [pytest.mark.requires_unix_sockets]

_WAIT_TIMEOUT = 10.0


class _FakeRequest:
    """Request-socket double recording the close operations it receives."""

    def __init__(self) -> None:
        """Start with no recorded operations."""
        self.shutdown_calls: list[int] = []
        self.close_calls = 0

    def shutdown(self, how: int) -> None:
        """Record a half-close, rejecting a mode a real socket refuses.

        Raises
        ------
        ValueError
            If *how* is not one of the standard shutdown modes.
        """
        if how not in {socket.SHUT_RD, socket.SHUT_WR, socket.SHUT_RDWR}:
            msg = f"invalid shutdown mode: {how}"
            raise ValueError(msg)
        self.shutdown_calls.append(how)

    def close(self) -> None:
        """Record a socket close."""
        self.close_calls += 1


class _InlineThread:
    """Thread double that runs its target on the calling thread."""

    def __init__(
        self,
        *,
        target: cabc.Callable[..., object],
        args: tuple[object, ...],
    ) -> None:
        """Store the target the real mixin would hand to a worker thread."""
        self._target = target
        self._args = args
        self.daemon = False

    def start(self) -> None:
        """Run the worker body inline, as an instantly finishing thread would."""
        self._target(*self._args)


def _fail_thread_start(_thread: threading.Thread) -> None:
    """Refuse to start a worker, as an exhausted thread budget would.

    Raises
    ------
    RuntimeError
        Always, with the message a real thread start failure reports.
    """
    msg = "can't start new thread"
    raise RuntimeError(msg)


def _wait_until(
    predicate: cabc.Callable[[], bool], *, message: str, timeout: float = _WAIT_TIMEOUT
) -> None:
    """Poll *predicate* until it holds, failing the test after *timeout*."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail(message)


def _held_slot_count(backend: object) -> int:
    """Return how many admitted requests currently hold an admission permit."""
    inner = typ.cast("typ.Any", backend)
    with inner._slots_lock:
        return len(inner._slots)


def _register_slot(backend: object, request: object) -> None:
    """Attach one admission permit to *request* as an admit would."""
    inner = typ.cast("typ.Any", backend)
    slot = acquire_client_slot(inner._admission)
    assert slot is not None, "the test semaphore had no free permit"
    with inner._slots_lock:
        inner._slots[request] = slot


def _assert_permit_returned(backend: object) -> None:
    """Check that exactly one permit is available and then return it."""
    inner = typ.cast("typ.Any", backend)
    assert inner._admission.acquire(blocking=False) is True, (
        "the admission permit was not returned"
    )
    inner._admission.release()


def _connect_and_request(socket_path: Path, command: str) -> socket.socket:
    """Connect, send one bare invocation, half-close, and return the client."""
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(_WAIT_TIMEOUT)
    _connect_with_retry(client, socket_path)
    payload = json.dumps({
        "command": command,
        "args": [],
        "stdin": "",
        "env": {},
    }).encode()
    client.sendall(payload)
    client.shutdown(socket.SHUT_WR)
    return client


def _connect_with_retry(client: socket.socket, socket_path: Path) -> None:
    """Connect to *socket_path*, retrying while the listen backlog is full.

    A saturation test opens more clients than the default backlog admits, so a
    burst can outrun the accept loop. Every retry still reaches the same
    admission gate, which is what the test measures.

    Raises
    ------
    BlockingIOError
        If the backlog stays full for the whole retry window.
    """
    deadline = time.monotonic() + _WAIT_TIMEOUT
    while True:
        try:
            client.connect(str(socket_path))
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
        else:
            return


def _read_frame(client: socket.socket) -> bytes:
    """Read the peer's complete reply, treating a reset as an empty reply."""
    chunks: list[bytes] = []
    while True:
        try:
            chunk = client.recv(4096)
        except ConnectionResetError:
            break
        if not chunk:
            break
        chunks.append(chunk)
    return b"".join(chunks)


@pytest.fixture
def running_backend(short_unix_socket_path: Path) -> cabc.Iterator[object]:
    """Run an inner backend directly, without the readiness probe connection."""
    outer = IPCServer(short_unix_socket_path)
    backend = _ipc_server._inner_server_cls()(short_unix_socket_path, outer)
    thread = threading.Thread(
        target=partial(backend.serve_forever, poll_interval=0.01),
        daemon=True,
    )
    thread.start()
    try:
        yield backend
    finally:
        backend.shutdown()
        backend.server_close()
        thread.join(_WAIT_TIMEOUT)


def test_unix_server_rejects_an_oversized_request_before_dispatch(
    short_unix_socket_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized request is refused without a response or a dispatch."""
    limit = 1024
    read_timeout = 2.0
    monkeypatch.setattr(_ipc_server, "MAX_MESSAGE_SIZE", limit)
    handled: list[Invocation] = []

    def handler(invocation: Invocation) -> Response:
        """Record any dispatch that the size check should have prevented."""
        handled.append(invocation)
        return Response(stdout="handled")

    with (
        _observability.capture_events() as events,
        IPCServer(
            short_unix_socket_path,
            timeout=read_timeout,
            handlers=IPCHandlers(handler=handler),
        ),
    ):
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.settimeout(read_timeout * 2)
            client.connect(str(short_unix_socket_path))
            # Deliberately no half-close: the server must reject the payload on
            # its own rather than waiting for EOF or its read deadline.
            sent_at = time.monotonic()
            client.sendall(b"x" * (limit + 1))
            reply = _read_frame(client)
            rejected_after = time.monotonic() - sent_at
        finally:
            client.close()

    assert reply == b"", "The refused request received a response frame"
    assert not handled, "An oversized request reached the callback"
    assert rejected_after < read_timeout, "Rejection waited for the read deadline"
    [event] = [
        item
        for item in events
        if item.operation == "ipc.server.connection"
        and item.outcome == "read_size_rejected"
    ]
    assert event.transport == "unix", "Size rejection omitted its transport"
    assert event.error_category == "MessageTooLargeError", "Wrong bounded category"
    assert event.message_size == limit + 1, "Event did not report the received size"
    assert event.correlation_id is None, "Unparsed requests must not be correlated"


def test_process_request_refuses_beyond_the_client_limit(
    running_backend: object,
) -> None:
    """A full admission gate closes the request without starting a worker."""
    backend = typ.cast("typ.Any", running_backend)
    backend._admission = threading.BoundedSemaphore(1)
    held = acquire_client_slot(backend._admission)
    assert held is not None, "the test semaphore had no free permit"
    request = _FakeRequest()

    try:
        with _observability.capture_events() as events:
            backend.process_request(request, "")
    finally:
        held.release()

    assert request.close_calls == 1, "The refused socket was not closed"
    assert _held_slot_count(backend) == 0, "A refused socket was admitted"
    [event] = [item for item in events if item.outcome == "rejected"]
    assert event.transport == "unix", "Admission refusal omitted its transport"
    assert event.error_category == "ClientLimitReached", "Wrong bounded category"
    assert event.duration_ms is None, "An admission refusal has no read duration"


def test_process_request_returns_the_permit_when_the_thread_cannot_start(
    running_backend: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed worker start releases the permit and re-raises the failure."""
    backend = typ.cast("typ.Any", running_backend)
    request = _FakeRequest()
    monkeypatch.setattr(threading.Thread, "start", _fail_thread_start)

    with (
        _observability.capture_events() as events,
        pytest.raises(RuntimeError, match="can't start new thread"),
    ):
        backend.process_request(request, "")

    assert _held_slot_count(backend) == 0, "The failed request kept its permit"
    _assert_permit_returned(backend)
    [event] = [item for item in events if item.outcome == "rejected"]
    assert event.error_category == "ThreadStartFailed", "Wrong bounded category"
    assert request.close_calls == 0, "The accept loop, not this call, closes it"


def test_accept_loop_closes_and_releases_after_a_thread_start_failure(
    running_backend: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The accept loop closes the socket and releases the permit it cannot use."""
    backend = typ.cast("typ.Any", running_backend)
    request = _FakeRequest()
    failures: list[BaseException | None] = []

    def record_error(_request: object, _address: object) -> None:
        """Record the failure the accept loop reports."""
        failures.append(sys.exc_info()[1])

    monkeypatch.setattr(backend, "get_request", lambda: (request, ""))
    monkeypatch.setattr(backend, "handle_error", record_error)
    monkeypatch.setattr(threading.Thread, "start", _fail_thread_start)

    with _observability.capture_events() as events:
        backend._handle_request_noblock()

    assert request.close_calls == 1, "The accept loop did not close the socket"
    assert _held_slot_count(backend) == 0, "The failed request kept its permit"
    _assert_permit_returned(backend)
    assert len(failures) == 1, "The accept loop did not report the failure"
    assert isinstance(failures[0], RuntimeError), "The wrong failure was reported"
    [event] = [item for item in events if item.outcome == "rejected"]
    assert event.error_category == "ThreadStartFailed", "Wrong bounded category"


def test_worker_completion_before_process_request_returns_releases_its_permit(
    running_backend: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker that finishes immediately still returns its permit exactly once."""
    backend = typ.cast("typ.Any", running_backend)
    request = _FakeRequest()
    finished: list[object] = []
    monkeypatch.setattr(threading, "Thread", _InlineThread)
    monkeypatch.setattr(
        backend, "finish_request", lambda served, _address: finished.append(served)
    )

    backend.process_request(request, "")

    assert finished == [request], "The worker did not serve the admitted request"
    assert request.close_calls == 1, "Worker completion did not close the request"
    assert _held_slot_count(backend) == 0, "Worker completion kept its permit"
    _assert_permit_returned(backend)


def test_worker_failure_still_closes_the_request_and_returns_its_permit(
    running_backend: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A handler failure is reported, closed, and released exactly once."""
    backend = typ.cast("typ.Any", running_backend)
    request = _FakeRequest()
    failures: list[BaseException | None] = []

    def fail_request(_request: object, _address: object) -> None:
        """Fail the worker before it can respond."""
        msg = "handler failed"
        raise RuntimeError(msg)

    def record_error(_request: object, _address: object) -> None:
        """Record the failure the worker reports."""
        failures.append(sys.exc_info()[1])

    monkeypatch.setattr(threading, "Thread", _InlineThread)
    monkeypatch.setattr(backend, "finish_request", fail_request)
    monkeypatch.setattr(backend, "handle_error", record_error)

    backend.process_request(request, "")

    assert len(failures) == 1, "The worker failure was not reported"
    assert isinstance(failures[0], RuntimeError), "The wrong failure was reported"
    assert request.close_calls == 1, "Worker failure did not close the request"
    assert _held_slot_count(backend) == 0, "Worker failure kept its permit"
    _assert_permit_returned(backend)


def test_shutdown_request_returns_the_permit_when_closing_fails(
    running_backend: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing close still returns the permit before the failure propagates."""
    backend = typ.cast("typ.Any", running_backend)
    request = _FakeRequest()
    _register_slot(backend, request)

    def fail_close(_server: object, _request: object) -> None:
        """Simulate a superclass close that fails."""
        msg = "close failed"
        raise OSError(msg)

    monkeypatch.setattr(
        socketserver.TCPServer,
        "shutdown_request",
        fail_close,
    )

    with pytest.raises(OSError, match="close failed"):
        backend.shutdown_request(request)

    assert _held_slot_count(backend) == 0, "A failed close kept its permit"
    _assert_permit_returned(backend)


def test_repeated_shutdown_releases_the_permit_once(running_backend: object) -> None:
    """A repeated cleanup is harmless and never over-releases the permit."""
    backend = typ.cast("typ.Any", running_backend)
    backend._admission = threading.BoundedSemaphore(1)
    request = _FakeRequest()
    _register_slot(backend, request)

    backend.shutdown_request(request)
    backend.shutdown_request(request)

    assert _held_slot_count(backend) == 0, "Repeated cleanup kept a permit entry"
    _assert_permit_returned(backend)


def _await_release(tokens: queue.Queue[None]) -> None:
    """Block a handler until one release arrives, failing after the wait bound."""
    try:
        tokens.get(timeout=_WAIT_TIMEOUT)
    except queue.Empty:
        pytest.fail("blocked handler never ran")


def test_unix_server_refuses_client_65_and_admits_after_a_permit_returns(
    short_unix_socket_path: Path,
) -> None:
    """A 65th client is closed without a frame until a permit returns."""
    # One queued token wakes exactly one blocked handler, which a semaphore
    # expresses less precisely because it also offers context-manager use.
    tokens: queue.Queue[None] = queue.Queue()
    draining = threading.Event()
    entered: list[str] = []
    entered_lock = threading.Lock()

    def handler(invocation: Invocation) -> Response:
        """Block until released, then echo the command name."""
        with entered_lock:
            entered.append(invocation.command)
        if not draining.is_set():
            _await_release(tokens)
        return Response(stdout=invocation.command)

    def entered_count() -> int:
        """Return how many callbacks have started."""
        with entered_lock:
            return len(entered)

    clients: list[socket.socket] = []
    with (
        _observability.capture_events() as events,
        IPCServer(
            short_unix_socket_path,
            timeout=_WAIT_TIMEOUT,
            handlers=IPCHandlers(handler=handler),
        ) as server,
    ):
        backend = typ.cast("typ.Any", server._server)
        try:
            clients.extend(
                _connect_and_request(short_unix_socket_path, f"cmd-{index}")
                for index in range(MAX_ACTIVE_CLIENTS)
            )
            # Every permit must be held by a blocked callback before the next
            # client arrives. Waiting for the last one also outlasts the
            # readiness probe start() makes, whose permit returns first.
            _wait_until(
                lambda: entered_count() == MAX_ACTIVE_CLIENTS,
                message="the server did not admit its full client allowance",
            )

            refused = _connect_and_request(short_unix_socket_path, "refused")
            clients.append(refused)
            assert _read_frame(refused) == b"", "Client 65 received a response frame"

            tokens.put(None)
            _wait_until(
                lambda: _held_slot_count(backend) == MAX_ACTIVE_CLIENTS - 1,
                message="the completed worker did not return its permit",
            )

            draining.set()
            admitted = _connect_and_request(short_unix_socket_path, "admitted")
            clients.append(admitted)
            reply = _read_frame(admitted)
        finally:
            draining.set()
            for _client in clients:
                tokens.put(None)
            for client in clients:
                client.close()

    assert json.loads(reply)["stdout"] == "admitted", (
        "A client was not admitted after a permit returned"
    )
    [rejection] = [
        item
        for item in events
        if item.operation == "ipc.server.connection" and item.outcome == "rejected"
    ]
    assert rejection.error_category == "ClientLimitReached", "Wrong bounded category"
    assert rejection.transport == "unix", "Admission refusal omitted its transport"
