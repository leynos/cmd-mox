"""Unit tests for IPC client helpers."""

from __future__ import annotations

import dataclasses as dc
import os
import pathlib
import socket
import threading
import time
import typing as typ

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from cmd_mox.environment import CMOX_IPC_SOCKET_ENV
from cmd_mox.ipc import client as ipc_client
from cmd_mox.ipc.client import (
    RetryConfig,
    _connect_unix_with_retries,
    _ConnectionContext,
    invoke_server,
    report_passthrough_result,
)
from cmd_mox.ipc.constants import KIND_INVOCATION, KIND_PASSTHROUGH_RESULT
from cmd_mox.ipc.models import Invocation, PassthroughResult, Response


class _FakeSocket:
    """Socket double that toggles between failure and success."""

    attempts: int = 0
    succeed_after: int = 1

    def __init__(self, *_: object, **__: object) -> None:
        self.closed = False

    def settimeout(self, _timeout: float) -> None:
        pass

    def connect(self, _address: str) -> None:
        type(self).attempts += 1
        if type(self).attempts < type(self).succeed_after:
            raise ConnectionRefusedError

    def close(self) -> None:
        self.closed = True


class _AlwaysFailSocket(_FakeSocket):
    succeed_after = 10


@pytest.fixture(autouse=True)
def _reset_fake_sockets() -> None:
    """Reset socket counters between tests."""
    _FakeSocket.attempts = 0
    _FakeSocket.succeed_after = 1
    _AlwaysFailSocket.attempts = 0
    _AlwaysFailSocket.succeed_after = 10


def test_retry_config_validates_inputs() -> None:
    """RetryConfig should reject invalid values eagerly."""
    with pytest.raises(ValueError, match="retries must be >= 1"):
        RetryConfig(retries=0)
    with pytest.raises(ValueError, match="backoff must be >= 0 and finite"):
        RetryConfig(backoff=-1)
    with pytest.raises(ValueError, match="jitter must be between 0 and 1"):
        RetryConfig(jitter=2.0)


@pytest.mark.parametrize(
    "value",
    [
        pytest.param(1.5, id="float"),
        pytest.param("3", id="str"),
        pytest.param(None, id="none"),
        pytest.param(True, id="bool"),
    ],
)
def test_retry_config_rejects_non_integer_retries(value: object) -> None:
    """RetryConfig should reject non-integer retry counts eagerly."""
    with pytest.raises(TypeError, match=r"^retries must be an integer$"):
        RetryConfig(retries=value)  # type: ignore[arg-type, ty:invalid-argument-type] - the test deliberately supplies non-integer retry counts


def test_retry_config_validate_checks_timeout() -> None:
    """RetryConfig.validate should validate timeout in addition to fields."""
    config = RetryConfig()
    with pytest.raises(ValueError, match="timeout must be > 0 and finite"):
        config.validate(0.0)


def test_connect_unix_with_retries_eventually_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """_connect_unix_with_retries should retry until the socket connects."""
    tmp_path = pathlib.Path(tmp_path)
    _FakeSocket.succeed_after = 2
    monkeypatch.setattr(socket, "socket", _FakeSocket)

    retry_config = RetryConfig(retries=3, backoff=0.0, jitter=0.0)
    sock = _connect_unix_with_retries(
        tmp_path / "ipc.sock",
        _ConnectionContext(timeout=0.1, retry_config=retry_config),
    )

    assert isinstance(sock, _FakeSocket), "Assertion failed"
    assert _FakeSocket.attempts == 2, "Assertion failed"


def test_connect_unix_with_retries_raises_after_exhaustion(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """_connect_unix_with_retries should raise the final OSError."""
    tmp_path = pathlib.Path(tmp_path)
    monkeypatch.setattr(socket, "socket", _AlwaysFailSocket)
    retry_config = RetryConfig(retries=2, backoff=0.0, jitter=0.0)

    with pytest.raises(ConnectionRefusedError, match=r"^$"):
        _connect_unix_with_retries(
            tmp_path / "ipc.sock",
            _ConnectionContext(timeout=0.1, retry_config=retry_config),
        )
    assert _AlwaysFailSocket.attempts == 2, "Assertion failed"


def test_invoke_server_uses_named_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """invoke_server should send invocation requests with the expected kind."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    captured: dict[str, typ.Any] = {}

    def fake_send(
        kind: str,
        data: dict[str, typ.Any],
        context: _ConnectionContext,
    ) -> Response:
        """Capture the serialized request context and return a test response.

        Returns
        -------
        Response
            The successful response expected by the invocation test.
        """
        captured["kind"] = kind
        captured["data"] = data
        assert context.retry_config.retries == 3, "Default retry config was not used"
        return Response(stdout="ok")

    monkeypatch.setattr("cmd_mox.ipc.client._send_request", fake_send)

    response = invoke_server(invocation, timeout=1.0, retry_config=None)

    assert response.stdout == "ok", "Assertion failed"
    assert captured["kind"] == KIND_INVOCATION, "Assertion failed"
    assert captured["data"] == invocation.to_dict(), "Assertion failed"


def test_invoke_server_forwards_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An absolute deadline reaches the shared request context."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    deadline = 123.5
    captured: dict[str, object] = {}

    def fake_send(
        kind: str,
        data: dict[str, typ.Any],
        context: _ConnectionContext,
    ) -> Response:
        """Capture the forwarded request and return a successful response.

        Returns
        -------
        Response
            The successful response expected by the invocation test.
        """
        captured["kind"] = kind
        captured["data"] = data
        captured["timeout"] = context.timeout
        captured["retry"] = context.retry_config
        captured["deadline"] = context.deadline
        return Response(stdout="ok")

    monkeypatch.setattr(ipc_client, "_send_request", fake_send)

    invoke_server(invocation, timeout=1.0, deadline=deadline)

    assert captured["deadline"] == deadline, "Invocation deadline was not forwarded"


def test_invoke_server_does_not_restart_expired_encoding_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Payload encoding cannot restart a shim's absolute IPC deadline."""
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    now = {"value": 100.0}

    def encode_after_deadline(
        _kind: str, _data: dict[str, typ.Any]
    ) -> tuple[bytes, str]:
        """Advance fake time during encoding before returning an envelope.

        Returns
        -------
        tuple[bytes, str]
            A minimal encoded request and its correlation identifier.
        """
        now["value"] = 102.0
        return b"{}", "correlation"

    def unexpected_socket(*_args: object, **_kwargs: object) -> typ.NoReturn:
        """Fail if an expired request proceeds to socket creation.

        Returns
        -------
        NoReturn
            Never returns; ``pytest.fail`` fails the test if called.
        """
        return pytest.fail("Socket creation must not follow an expired deadline")

    monkeypatch.setattr(ipc_client, "_build_request_envelope", encode_after_deadline)
    monkeypatch.setattr(
        ipc_client, "_get_validated_socket_path", lambda: tmp_path / "ipc.sock"
    )
    monkeypatch.setattr(ipc_client.time, "monotonic", lambda: now["value"])
    monkeypatch.setattr(ipc_client.path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(ipc_client.socket, "socket", unexpected_socket)

    with pytest.raises(TimeoutError):
        invoke_server(invocation, timeout=1.0, deadline=101.0)


@pytest.mark.skipif(os.name == "nt", reason="Unix socket deadline behaviour")
def test_invoke_server_bounds_fragmented_response_to_one_deadline(
    monkeypatch: pytest.MonkeyPatch, short_unix_socket_path: pathlib.Path
) -> None:
    """Slow response chunks cannot renew the Unix client's I/O budget."""
    socket_path = short_unix_socket_path
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(socket_path))
    listener.listen(1)
    accepted = threading.Event()
    release = threading.Event()

    def serve() -> None:
        """Receive the request and stream a response in delayed fragments."""
        try:
            connection, _address = listener.accept()
            with connection:
                while connection.recv(64 * 1024):
                    pass
                accepted.set()
                for chunk in (
                    b'{"stdout":',
                    b'"slow"',
                    b',"stderr":""',
                    b',"exit_code":0,"env":{}}',
                ):
                    if release.wait(0.05):
                        return
                    connection.sendall(chunk)
        except OSError:
            # The client closes its side when its shared deadline expires.
            pass

    server_thread = threading.Thread(target=serve, daemon=True)
    server_thread.start()
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, str(socket_path))
    invocation = Invocation(command="cmd", args=[], stdin="", env={})
    started = time.monotonic()

    try:
        with pytest.raises(TimeoutError):
            invoke_server(
                invocation,
                timeout=0.12,
                retry_config=RetryConfig(retries=1, backoff=0.0, jitter=0.0),
            )

        elapsed = time.monotonic() - started
        assert accepted.wait(1.0), "Unix peer did not receive the request"
        assert elapsed < 0.8, "Client response read exceeded its single deadline"
    finally:
        release.set()
        listener.close()
        server_thread.join(timeout=1.0)

    assert not server_thread.is_alive(), "Unix response peer did not stop"


@pytest.mark.skipif(os.name == "nt", reason="Unix socket retry behaviour")
def test_unix_connect_retry_sleep_is_capped_by_the_deadline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Retry backoff stays within one deadline and skips later attempts."""
    clock = {"value": 10.0}
    created: list[float] = []
    connected: list[float] = []
    timeouts: list[float] = []
    sleeps: list[float] = []

    class _ClockedSocket:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            """Record when each retry attempt creates its socket."""
            created.append(clock["value"])

        def settimeout(self, timeout: float) -> None:
            """Record the remaining timeout assigned to the socket."""
            timeouts.append(timeout)

        def connect(self, _address: str) -> None:
            """Record the attempt time and simulate a refused connection.

            Raises
            ------
            ConnectionRefusedError
                Simulates the unavailable Unix socket at each retry attempt.
            """
            connected.append(clock["value"])
            raise ConnectionRefusedError

        def close(self) -> None:
            """Accept cleanup after the simulated connection refusal."""
            pass

    def advance_clock(delay: float) -> None:
        """Replace retry sleeping with deterministic fake-clock progress."""
        sleeps.append(delay)
        clock["value"] += delay

    monkeypatch.setattr(ipc_client.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(ipc_client.time, "sleep", advance_clock)
    monkeypatch.setattr(ipc_client.socket, "socket", _ClockedSocket)
    context = _ConnectionContext(
        timeout=0.05,
        retry_config=RetryConfig(retries=5, backoff=0.04, jitter=0.0),
    )

    with pytest.raises(TimeoutError):
        _connect_unix_with_retries(tmp_path / "missing.sock", context)

    assert created == pytest.approx([10.0, 10.04]), (
        "A new socket attempt started after the deadline"
    )
    assert connected == pytest.approx([10.0, 10.04]), (
        "A connection attempt started after the deadline"
    )
    assert timeouts == pytest.approx([0.05, 0.01]), (
        "Connect timeouts did not use the remaining deadline"
    )
    assert sleeps == pytest.approx([0.04, 0.01]), (
        "Retry sleeps were not capped to the remaining deadline"
    )


def test_report_passthrough_result_uses_named_kind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """report_passthrough_result should send responses with the expected kind."""
    result = PassthroughResult(invocation_id="1", stdout="", stderr="", exit_code=0)
    captured: dict[str, typ.Any] = {}

    def fake_send(
        kind: str,
        data: dict[str, typ.Any],
        context: _ConnectionContext,
    ) -> Response:
        """Capture a passthrough report without opening an IPC socket.

        Returns
        -------
        Response
            A representative successful response.
        """
        captured["kind"] = kind
        captured["data"] = data
        return Response(stdout="ok")

    monkeypatch.setattr("cmd_mox.ipc.client._send_request", fake_send)

    report_passthrough_result(result, timeout=1.0)

    assert captured["kind"] == KIND_PASSTHROUGH_RESULT, "Assertion failed"
    assert captured["data"] == result.to_dict(), "Assertion failed"


def test_report_passthrough_result_forwards_absolute_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passthrough reports can share their caller's absolute deadline."""
    result = PassthroughResult(invocation_id="1", stdout="", stderr="", exit_code=0)
    deadline = 123.5
    captured: dict[str, object] = {}

    def fake_send(
        kind: str,
        data: dict[str, typ.Any],
        context: _ConnectionContext,
    ) -> Response:
        """Capture the passthrough request deadline and return a response.

        Returns
        -------
        Response
            The successful response expected by the passthrough test.
        """
        captured["deadline"] = context.deadline
        return Response(stdout="ok")

    monkeypatch.setattr(ipc_client, "_send_request", fake_send)

    report_passthrough_result(result, timeout=1.0, deadline=deadline)

    assert captured["deadline"] == deadline, "Passthrough deadline was not forwarded"


class _DeadlineClock:
    """Fake monotonic clock for generated IPC operation schedules."""

    def __init__(self, deadline: float) -> None:
        self.deadline = deadline
        self.now = 0.0
        self.operation_starts: list[tuple[str, float, float]] = []
        self.sleep_intervals: list[tuple[float, float]] = []

    def remaining(self, request_deadline: float) -> float:
        """Return time left on the one request deadline.

        Returns
        -------
        float
            Positive seconds remaining in the request budget.

        Raises
        ------
        TimeoutError
            The request deadline has expired.
        """
        assert request_deadline == self.deadline, "The request changed its deadline"
        remaining = request_deadline - self.now
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def consume(self, operation: str, elapsed: float, timeout: float | None) -> None:
        """Advance fake time for one blocking socket operation.

        Raises
        ------
        TimeoutError
            The operation cannot finish within its assigned socket timeout.
        """
        assert timeout is not None, "Blocking I/O had no socket timeout"
        start = self.now
        remaining = self.remaining(self.deadline)
        assert timeout == pytest.approx(remaining), (
            "Socket timeout did not use the shared deadline"
        )
        self.operation_starts.append((operation, start, timeout))
        if elapsed >= remaining:
            self.now = self.deadline
            raise TimeoutError
        self.now += elapsed

    def sleep(self, delay: float) -> None:
        """Advance fake time for retry backoff within the remaining budget."""
        start = self.now
        remaining = self.remaining(self.deadline)
        assert delay <= remaining, "Retry backoff exceeded the shared deadline"
        self.sleep_intervals.append((start, delay))
        self.now += delay


@dc.dataclass(frozen=True, slots=True)
class _DeadlineScenario:
    """Generated timings and retry outcomes for one IPC request."""

    failed_connects: int
    backoff: float
    connect_elapsed: tuple[float, float, float]
    send_elapsed: float
    shutdown_elapsed: float
    response_chunks: int
    recv_elapsed: tuple[float, float, float, float]


def _deadline_scenario_strategy() -> st.SearchStrategy[_DeadlineScenario]:
    """Generate bounded socket-phase timings and retry counts.

    Returns
    -------
    hypothesis.strategies.SearchStrategy[_DeadlineScenario]
        Scenarios that vary retries, blocking phases, and expiry order.
    """
    elapsed = st.floats(min_value=0.0, max_value=1.25, allow_nan=False)
    return st.builds(
        _DeadlineScenario,
        failed_connects=st.integers(min_value=0, max_value=2),
        backoff=elapsed,
        connect_elapsed=st.tuples(elapsed, elapsed, elapsed),
        send_elapsed=elapsed,
        shutdown_elapsed=elapsed,
        response_chunks=st.integers(min_value=0, max_value=3),
        recv_elapsed=st.tuples(elapsed, elapsed, elapsed, elapsed),
    )


class _ScriptedUnixSocket:
    """Socket double driven by a generated sequence of operation durations."""

    def __init__(
        self,
        clock: _DeadlineClock,
        attempt: int,
        scenario: _DeadlineScenario,
    ) -> None:
        self.clock = clock
        self.attempt = attempt
        self.scenario = scenario
        self.timeout: float | None = None
        self.received = 0

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def connect(self, _address: str) -> None:
        self.clock.consume(
            "connect", self.scenario.connect_elapsed[self.attempt], self.timeout
        )
        if self.attempt < self.scenario.failed_connects:
            raise ConnectionRefusedError

    def sendall(self, _payload: bytes) -> None:
        self.clock.consume("send", self.scenario.send_elapsed, self.timeout)

    def shutdown(self, _how: int) -> None:
        self.clock.consume("shutdown", self.scenario.shutdown_elapsed, self.timeout)

    def recv(self, _size: int) -> bytes:
        self.clock.consume(
            "recv", self.scenario.recv_elapsed[self.received], self.timeout
        )
        if self.received >= self.scenario.response_chunks:
            return b""
        chunk = f"response-{self.received}".encode()
        self.received += 1
        return chunk

    def close(self) -> None:
        pass

    def __enter__(self) -> _ScriptedUnixSocket:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _ScriptedUnixSocketFactory:
    """Create sockets from the generated request-phase scenario."""

    def __init__(
        self,
        clock: _DeadlineClock,
        scenario: _DeadlineScenario,
    ) -> None:
        self.clock = clock
        self.scenario = scenario
        self.attempts = 0

    def __call__(self, *_args: object, **_kwargs: object) -> _ScriptedUnixSocket:
        """Return the next scripted socket.

        Returns
        -------
        _ScriptedUnixSocket
            A socket configured for this connection attempt.
        """
        sock = _ScriptedUnixSocket(
            self.clock,
            self.attempts,
            self.scenario,
        )
        self.attempts += 1
        return sock


@pytest.mark.skipif(os.name == "nt", reason="Unix socket deadline behaviour")
@settings(max_examples=60, deadline=None)
@example(
    scenario=_DeadlineScenario(
        failed_connects=0,
        backoff=0.0,
        connect_elapsed=(1.25, 0.0, 0.0),
        send_elapsed=0.0,
        shutdown_elapsed=0.0,
        response_chunks=0,
        recv_elapsed=(0.0, 0.0, 0.0, 0.0),
    ),
)
@example(
    scenario=_DeadlineScenario(
        failed_connects=1,
        backoff=1.25,
        connect_elapsed=(0.0, 0.0, 0.0),
        send_elapsed=0.0,
        shutdown_elapsed=0.0,
        response_chunks=0,
        recv_elapsed=(0.0, 0.0, 0.0, 0.0),
    ),
)
@example(
    scenario=_DeadlineScenario(
        failed_connects=0,
        backoff=0.0,
        connect_elapsed=(0.0, 0.0, 0.0),
        send_elapsed=1.25,
        shutdown_elapsed=0.0,
        response_chunks=0,
        recv_elapsed=(0.0, 0.0, 0.0, 0.0),
    ),
)
@example(
    scenario=_DeadlineScenario(
        failed_connects=0,
        backoff=0.0,
        connect_elapsed=(0.0, 0.0, 0.0),
        send_elapsed=0.0,
        shutdown_elapsed=1.25,
        response_chunks=0,
        recv_elapsed=(0.0, 0.0, 0.0, 0.0),
    ),
)
@example(
    scenario=_DeadlineScenario(
        failed_connects=0,
        backoff=0.0,
        connect_elapsed=(0.0, 0.0, 0.0),
        send_elapsed=0.0,
        shutdown_elapsed=0.0,
        response_chunks=2,
        recv_elapsed=(1.25, 0.0, 0.0, 0.0),
    ),
)
@example(
    scenario=_DeadlineScenario(
        failed_connects=1,
        backoff=0.1,
        connect_elapsed=(0.05, 0.05, 0.0),
        send_elapsed=0.05,
        shutdown_elapsed=0.05,
        response_chunks=2,
        recv_elapsed=(0.05, 0.05, 0.05, 0.0),
    ),
)
@given(scenario=_deadline_scenario_strategy())
def test_unix_request_shares_deadline_across_generated_io_phases(
    scenario: _DeadlineScenario,
) -> None:
    """One absolute deadline bounds generated connect, send, and read phases."""
    deadline = 1.0
    clock = _DeadlineClock(deadline)
    socket_factory = _ScriptedUnixSocketFactory(clock, scenario)
    context = _ConnectionContext(
        timeout=deadline,
        retry_config=RetryConfig(
            retries=scenario.failed_connects + 1,
            backoff=scenario.backoff,
            jitter=0.0,
        ),
        deadline=deadline,
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(ipc_client, "_remaining_time", clock.remaining)
        monkeypatch.setattr(ipc_client.socket, "socket", socket_factory)
        monkeypatch.setattr(ipc_client.time, "sleep", clock.sleep)
        try:
            response = ipc_client._send_unix_request(
                pathlib.Path("ipc.sock"), b"request", context
            )
        except TimeoutError:
            response = None

    assert clock.now <= deadline, "The request exceeded its shared deadline"
    assert all(
        start < deadline for _operation, start, _timeout in clock.operation_starts
    ), "A blocking operation began after the request deadline"
    assert all(delay <= deadline - start for start, delay in clock.sleep_intervals), (
        "Retry sleep exceeded the remaining request budget"
    )
    if clock.now == deadline:
        assert response is None, "The request succeeded after exhausting its deadline"
    if response is not None:
        assert clock.now < deadline, "The request completed at its deadline"
        expected = b"".join(
            f"response-{index}".encode() for index in range(scenario.response_chunks)
        )
        assert response == expected, "The successful request lost response fragments"
