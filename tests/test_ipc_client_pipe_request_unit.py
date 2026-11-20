"""Unit tests for the Windows IPC client's pipe request helpers.

These tests run on every platform: the pywin32 modules are replaced with
recording stand-ins, so handle creation, per-phase deadlines, cancellation, and
exactly-once closing are exercised without native named pipes.
"""

from __future__ import annotations

import dataclasses
import pathlib
import types
import typing as typ

import pytest
from hypothesis import given
from hypothesis import strategies as st

from cmd_mox.ipc import client, windows
from cmd_mox.ipc.client import RetryConfig, _ConnectionContext
from cmd_mox.ipc.windows import MAX_MESSAGE_SIZE, PIPE_CHUNK_SIZE, PipeReadOptions

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_TIMEOUT_MESSAGE = "IPC client operation timed out"


class _RecordingBlockingRunner:
    """Run blocking closures synchronously while recording their phases."""

    def __init__(self) -> None:
        """Start with no recorded phases."""
        self.deadlines: list[float] = []
        self.cancels: list[cabc.Callable[[], None]] = []

    def __call__(
        self,
        func: cabc.Callable[[], object],
        *,
        deadline: float,
        cancel: cabc.Callable[[], None],
    ) -> object:
        """Record the phase, run the closure and return its value."""
        self.deadlines.append(deadline)
        self.cancels.append(cancel)
        return func()


class _FakeClock:
    """Monotonic clock whose reading the test advances explicitly."""

    def __init__(self, now: float = 0.0) -> None:
        """Start the clock at *now*."""
        self.now = now

    def remaining(self, deadline: float) -> float:
        """Report the budget left, raising once it is spent."""
        left = deadline - self.now
        if left <= 0:
            raise TimeoutError(_TIMEOUT_MESSAGE)
        return left


class _BudgetRecordingRunner:
    """Run blocking closures while advancing a fake clock past each phase."""

    def __init__(self, clock: _FakeClock, advance: float) -> None:
        """Record the clock and how far each phase advances it."""
        self.clock = clock
        self.advance = advance
        self.budgets: list[float] = []

    def __call__(
        self,
        func: cabc.Callable[[], object],
        *,
        deadline: float,
        cancel: cabc.Callable[[], None],
    ) -> object:
        """Record the budget the phase sees, advance the clock, then run it."""
        time_left = self.clock.remaining(deadline)
        self.budgets.append(time_left)
        self.clock.now += self.advance
        return func()


@dataclasses.dataclass(slots=True)
class _SendPipeSpy:
    """Recorded artefacts of one ``_send_pipe_request`` call."""

    handle: object
    connects: list[tuple[str, object, float]]
    writes: list[tuple[object, bytes, object]]
    reads: list[tuple[object, object, object, PipeReadOptions]]
    closes: list[object]


def _bind_send_pipe_request(
    monkeypatch: pytest.MonkeyPatch,
) -> _SendPipeSpy:
    """Install recording doubles for one ``_send_pipe_request`` call."""
    spy = _SendPipeSpy(handle=object(), connects=[], writes=[], reads=[], closes=[])

    def connect(pipe_name: str, context: object, *, deadline: float) -> object:
        """Record the connection arguments and return the shared handle."""
        spy.connects.append((pipe_name, context, deadline))
        return spy.handle

    def write(h: object, payload: bytes, *, win32file: object) -> None:
        """Record the write arguments."""
        spy.writes.append((h, payload, win32file))

    def read(
        h: object, *, win32file: object, pywintypes: object, options: PipeReadOptions
    ) -> bytes:
        """Record the read arguments and return a fixed reply."""
        spy.reads.append((h, win32file, pywintypes, options))
        return b"reply"

    monkeypatch.setattr(client, "pywintypes", types.SimpleNamespace(error=RuntimeError))
    monkeypatch.setattr(client, "derive_pipe_name", lambda _path: "hashed-pipe")
    monkeypatch.setattr(client, "_connect_pipe_with_retries", connect)
    monkeypatch.setattr(client, "write_pipe_payload", write)
    monkeypatch.setattr(client, "read_pipe_message", read)
    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(CloseHandle=spy.closes.append),
    )
    return spy


@pytest.fixture
def send_pipe_spy(monkeypatch: pytest.MonkeyPatch) -> _SendPipeSpy:
    """Install the recording transport doubles and return their spy."""
    return _bind_send_pipe_request(monkeypatch)


def test_send_pipe_request_shares_one_deadline_across_every_phase(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Connect, write and read draw down a single computed deadline."""
    runner = _RecordingBlockingRunner()
    computed: list[float] = []

    def compute(timeout: float) -> float:
        """Record the timeout and return one fixed deadline."""
        computed.append(timeout)
        return 101.0

    monkeypatch.setattr(client, "_run_blocking_io", runner)
    monkeypatch.setattr(client, "compute_deadline", compute)
    context = _ConnectionContext(timeout=2.0, retry_config=RetryConfig())

    result = client._send_pipe_request(pathlib.Path("socket"), b"payload", context)

    assert result == b"reply", "the pipe reply was not returned"
    assert computed == [2.0], "the deadline was not computed exactly once"
    assert runner.deadlines == [101.0, 101.0], (
        "the write and read phases did not share the computed deadline"
    )
    assert send_pipe_spy.connects == [("hashed-pipe", context, 101.0)], (
        "the connection did not use the hashed pipe name and shared deadline"
    )
    assert send_pipe_spy.writes == [
        (send_pipe_spy.handle, b"payload", client.win32file)
    ], "the write did not forward the handle, payload and Win32 module"
    assert len(send_pipe_spy.reads) == 1, "the read phase did not run exactly once"
    assert send_pipe_spy.reads[0][:3] == (
        send_pipe_spy.handle,
        client.win32file,
        client.pywintypes,
    ), "the read did not use the connected handle and Win32 modules"
    options = send_pipe_spy.reads[0][3]
    assert options.chunk_size == PIPE_CHUNK_SIZE, "the read chunk size changed"
    assert options.max_bytes == MAX_MESSAGE_SIZE, "the read size limit changed"
    assert send_pipe_spy.closes == [send_pipe_spy.handle], (
        "the pipe handle was not closed exactly once"
    )


def test_send_pipe_request_spends_one_budget_across_write_and_read(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each later phase sees less of the shared budget than the one before."""
    clock = _FakeClock()
    runner = _BudgetRecordingRunner(clock, advance=0.4)
    monkeypatch.setattr(client, "_run_blocking_io", runner)
    monkeypatch.setattr(client, "compute_deadline", lambda _timeout: 1.0)
    monkeypatch.setattr(client, "remaining", clock.remaining)

    client._send_pipe_request(
        pathlib.Path("socket"),
        b"payload",
        _ConnectionContext(timeout=1.0, retry_config=RetryConfig()),
    )

    assert runner.budgets == [pytest.approx(1.0), pytest.approx(0.6)], (
        "the write and read phases did not draw down one shared budget"
    )


def test_send_pipe_request_stops_when_the_shared_budget_is_spent(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A budget spent by the write phase prevents the reply read entirely."""
    clock = _FakeClock()
    runner = _BudgetRecordingRunner(clock, advance=1.5)
    monkeypatch.setattr(client, "_run_blocking_io", runner)
    monkeypatch.setattr(client, "compute_deadline", lambda _timeout: 1.0)
    monkeypatch.setattr(client, "remaining", clock.remaining)

    with pytest.raises(TimeoutError):
        client._send_pipe_request(
            pathlib.Path("socket"),
            b"payload",
            _ConnectionContext(timeout=1.0, retry_config=RetryConfig()),
        )

    assert send_pipe_spy.writes, "the write phase did not run"
    assert not send_pipe_spy.reads, "the expired budget still read the reply"
    assert send_pipe_spy.closes == [send_pipe_spy.handle], (
        "the handle was not closed after the expiry"
    )


@pytest.mark.parametrize("deadline", [42.0, 0.0], ids=["future", "zero"])
def test_send_pipe_request_reuses_the_context_deadline_verbatim(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
    deadline: float,
) -> None:
    """A supplied deadline is never recomputed, even when it reads as zero."""
    runner = _RecordingBlockingRunner()
    computed: list[float] = []
    monkeypatch.setattr(client, "_run_blocking_io", runner)
    monkeypatch.setattr(client, "compute_deadline", computed.append)
    context = _ConnectionContext(
        timeout=2.0, retry_config=RetryConfig(), deadline=deadline
    )

    client._send_pipe_request(pathlib.Path("socket"), b"payload", context)

    assert not computed, "a supplied deadline was replaced by a fresh one"
    assert runner.deadlines == [deadline, deadline], (
        "the phases did not share the supplied deadline"
    )
    assert send_pipe_spy.connects[0][2] == deadline, (
        "the connection did not use the supplied deadline"
    )


@dataclasses.dataclass(slots=True)
class _FailingPhaseCase:
    """One phase failing, with the observables that must still hold."""

    phase: str
    patch_target: str
    expected_writes: int
    expected_phases: int


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _FailingPhaseCase("write", "write_pipe_payload", 0, 1), id="write"
        ),
        pytest.param(_FailingPhaseCase("read", "read_pipe_message", 1, 2), id="read"),
    ],
)
def test_send_pipe_request_closes_once_when_a_phase_fails(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
    case: _FailingPhaseCase,
) -> None:
    """A failing write or read skips later phases and closes the handle once."""
    runner = _RecordingBlockingRunner()
    error = RuntimeError(f"{case.phase} failed")

    def fail(*_args: object, **_kwargs: object) -> object:
        """Raise the sentinel failure standing in for the broken phase."""
        raise error

    monkeypatch.setattr(client, "_run_blocking_io", runner)
    monkeypatch.setattr(client, "compute_deadline", lambda _timeout: 10.0)
    monkeypatch.setattr(client, case.patch_target, fail)

    with pytest.raises(RuntimeError) as excinfo:
        client._send_pipe_request(
            pathlib.Path("socket"),
            b"payload",
            _ConnectionContext(timeout=1.0, retry_config=RetryConfig()),
        )

    assert excinfo.value is error, "the phase error was replaced"
    assert len(send_pipe_spy.writes) == case.expected_writes, (
        "the write phase did not run exactly when the failure allowed it"
    )
    assert not send_pipe_spy.reads, "the recording read ran against the failure"
    assert len(runner.deadlines) == case.expected_phases, (
        "the phases after the failure still ran"
    )
    assert send_pipe_spy.closes == [send_pipe_spy.handle], (
        "the handle was not closed exactly once"
    )


def test_send_pipe_request_writes_and_reads_through_win32_stand_ins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real write and read helpers exchange a message over a stand-in pipe."""
    written: list[str] = []

    class _FakeHandle:
        """Record whether the fake pipe handle has been closed."""

        def __init__(self) -> None:
            """Start with the handle open."""
            self.closed = False

    handle = _FakeHandle()
    monkeypatch.setattr(
        client, "_connect_pipe_with_retries", lambda *_args, **_kwargs: handle
    )
    monkeypatch.setattr(
        client,
        "pywintypes",
        types.SimpleNamespace(error=RuntimeError),
    )
    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(
            WriteFile=lambda _handle, payload: written.append(payload.decode("utf-8")),
            FlushFileBuffers=lambda *_args, **_kwargs: None,
            CloseHandle=lambda target: setattr(target, "closed", True),
            ReadFile=lambda *_args, **_kwargs: (0, b"{}"),
        ),
    )

    response = client._send_pipe_request(
        pathlib.Path("pipe"),
        b"{}",
        # The phases touch only stand-ins, so a generous budget removes the
        # chance that thread startup on a loaded host consumes it.
        _ConnectionContext(timeout=5.0, retry_config=RetryConfig()),
    )

    assert response == b"{}", "the reply was not returned verbatim"
    assert written == ["{}"], "the payload did not reach the pipe writer"
    assert handle.closed is True, "the handle was not closed after the exchange"


def test_send_pipe_request_closes_once_when_a_phase_is_cancelled(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation and the finally block share one idempotent close."""
    closes: list[object] = []
    monkeypatch.setattr(client, "compute_deadline", lambda _timeout: 10.0)

    def cancel_then_raise(
        _func: cabc.Callable[[], object],
        *,
        deadline: float,
        cancel: cabc.Callable[[], None],
    ) -> object:
        """Cancel without running the phase, then report a timeout."""
        del deadline
        cancel()
        raise TimeoutError(_TIMEOUT_MESSAGE)

    monkeypatch.setattr(client, "_run_blocking_io", cancel_then_raise)
    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(CloseHandle=closes.append),
    )

    with pytest.raises(TimeoutError):
        client._send_pipe_request(
            pathlib.Path("socket"),
            b"payload",
            _ConnectionContext(timeout=1.0, retry_config=RetryConfig()),
        )

    assert not send_pipe_spy.writes, "the write body ran after cancellation"
    assert not send_pipe_spy.reads, "the read ran after cancellation"
    assert closes == [send_pipe_spy.handle], (
        "the cancellation close and the finally close were not exactly-once"
    )


def test_send_pipe_request_offers_cancellation_to_the_read_phase(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both phases receive the same handle-closing cancellation callable."""
    monkeypatch.setattr(client, "compute_deadline", lambda _timeout: 10.0)
    cancels: list[object] = []

    def run_then_time_out(
        func: cabc.Callable[[], object],
        *,
        deadline: float,
        cancel: cabc.Callable[[], None],
    ) -> object:
        """Run the write phase, then time out the read phase."""
        del deadline
        cancels.append(cancel)
        if len(cancels) == 1:
            return func()
        raise TimeoutError(_TIMEOUT_MESSAGE)

    monkeypatch.setattr(client, "_run_blocking_io", run_then_time_out)

    with pytest.raises(TimeoutError):
        client._send_pipe_request(
            pathlib.Path("socket"),
            b"payload",
            _ConnectionContext(timeout=1.0, retry_config=RetryConfig()),
        )

    assert len(cancels) == 2, "both phases must be given a cancellation callable"
    assert cancels[0] is not None, "the write phase lost its cancellation"
    assert cancels[1] == cancels[0], "the read phase lost its cancellation"
    assert send_pipe_spy.writes, "the write body never ran"
    assert not send_pipe_spy.reads, "the read body ran after the timeout"
    assert send_pipe_spy.closes == [send_pipe_spy.handle], (
        "the handle was not closed exactly once"
    )


@pytest.mark.parametrize(
    ("winerror", "attempt", "max_retries", "expected"),
    [
        pytest.param(windows.ERROR_PIPE_BUSY, 0, 3, True, id="busy-first"),
        pytest.param(windows.ERROR_FILE_NOT_FOUND, 1, 3, True, id="missing-second"),
        pytest.param(windows.ERROR_PIPE_BUSY, 2, 3, False, id="busy-last"),
        pytest.param(windows.ERROR_PIPE_BUSY, 0, 1, False, id="single-attempt"),
        pytest.param(windows.ERROR_NO_DATA, 0, 3, False, id="non-retryable-code"),
        pytest.param(None, 0, 3, False, id="missing-winerror"),
    ],
)
def test_should_retry_pipe_error_checks_code_and_remaining_attempts(
    winerror: int | None, attempt: int, max_retries: int, *, expected: bool
) -> None:
    """Only busy or missing pipes with attempts remaining are retried."""
    exc = types.SimpleNamespace(winerror=winerror)

    assert client._should_retry_pipe_error(exc, attempt, max_retries) is expected, (
        "the retry decision was wrong"
    )


@given(
    winerror=st.sampled_from([
        windows.ERROR_PIPE_BUSY,
        windows.ERROR_FILE_NOT_FOUND,
        windows.ERROR_NO_DATA,
        0,
        None,
    ]),
    attempt=st.integers(min_value=0, max_value=10),
    max_retries=st.integers(min_value=0, max_value=10),
)
def test_should_retry_pipe_error_matches_the_documented_rule(
    winerror: int | None, attempt: int, max_retries: int
) -> None:
    """Retrying happens exactly for a retryable code with attempts remaining."""
    retryable = winerror in {windows.ERROR_PIPE_BUSY, windows.ERROR_FILE_NOT_FOUND}
    expected = retryable and attempt < max_retries - 1

    decision = client._should_retry_pipe_error(
        types.SimpleNamespace(winerror=winerror), attempt, max_retries
    )

    assert decision is expected, "the retry decision diverged from the rule"


def test_should_retry_pipe_error_rejects_errors_without_a_winerror() -> None:
    """An error carrying no Windows code is never retried."""
    assert client._should_retry_pipe_error(object(), 0, 3) is False, (
        "an error without a winerror was retried"
    )
