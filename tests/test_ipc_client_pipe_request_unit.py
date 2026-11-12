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


def test_send_pipe_request_uses_separate_phase_deadlines_and_exact_arguments(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Write and read phases each get a fresh deadline and the pipe handle."""
    runner = _RecordingBlockingRunner()
    computed: list[float] = []

    def compute(timeout: float) -> float:
        """Record each timeout and return a distinct increasing deadline."""
        computed.append(timeout)
        return 100.0 + len(computed)

    monkeypatch.setattr(client, "_run_blocking_io", runner)
    monkeypatch.setattr(client, "_compute_deadline", compute)
    context = _ConnectionContext(timeout=2.0, retry_config=RetryConfig())

    result = client._send_pipe_request(pathlib.Path("socket"), b"payload", context)

    assert result == b"reply", "the pipe reply was not returned"
    assert computed == [2.0, 2.0, 2.0], "phase deadlines used the wrong timeout"
    assert runner.deadlines == [102.0, 103.0], (
        "the write and read phases did not receive separate deadlines"
    )
    assert send_pipe_spy.connects == [("hashed-pipe", context, 101.0)], (
        "the connection did not use the hashed pipe name and connect deadline"
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


def test_send_pipe_request_closes_once_when_the_write_fails(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed write skips the read and still closes the handle once."""
    runner = _RecordingBlockingRunner()
    error = RuntimeError("write failed")
    monkeypatch.setattr(client, "_run_blocking_io", runner)
    monkeypatch.setattr(client, "_compute_deadline", lambda _timeout: 10.0)

    def fail_write(_handle: object, _payload: bytes, *, win32file: object) -> None:
        """Raise the sentinel write failure."""
        raise error

    monkeypatch.setattr(client, "write_pipe_payload", fail_write)

    with pytest.raises(RuntimeError) as excinfo:
        client._send_pipe_request(
            pathlib.Path("socket"),
            b"payload",
            _ConnectionContext(timeout=1.0, retry_config=RetryConfig()),
        )

    assert excinfo.value is error, "the write error was replaced"
    assert not send_pipe_spy.reads, "the read ran after a failed write"
    assert send_pipe_spy.closes == [send_pipe_spy.handle], (
        "the handle was not closed exactly once"
    )


def test_send_pipe_request_closes_once_when_the_read_fails(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed read closes the handle exactly once after the write."""
    runner = _RecordingBlockingRunner()
    error = RuntimeError("read failed")
    monkeypatch.setattr(client, "_run_blocking_io", runner)
    monkeypatch.setattr(client, "_compute_deadline", lambda _timeout: 10.0)

    def fail_read(
        _handle: object, *, win32file: object, pywintypes: object, options: object
    ) -> bytes:
        """Raise the sentinel read failure."""
        raise error

    monkeypatch.setattr(client, "read_pipe_message", fail_read)

    with pytest.raises(RuntimeError) as excinfo:
        client._send_pipe_request(
            pathlib.Path("socket"),
            b"payload",
            _ConnectionContext(timeout=1.0, retry_config=RetryConfig()),
        )

    assert excinfo.value is error, "the read error was replaced"
    assert len(send_pipe_spy.writes) == 1, "the write did not run before the read"
    assert send_pipe_spy.closes == [send_pipe_spy.handle], (
        "the handle was not closed exactly once"
    )


def test_send_pipe_request_closes_once_when_a_phase_is_cancelled(
    send_pipe_spy: _SendPipeSpy,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation and the finally block share one idempotent close."""
    closes: list[object] = []
    monkeypatch.setattr(client, "_compute_deadline", lambda _timeout: 10.0)

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
    monkeypatch.setattr(client, "_compute_deadline", lambda _timeout: 10.0)
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


def test_should_retry_pipe_error_rejects_errors_without_a_winerror() -> None:
    """An error carrying no Windows code is never retried."""
    assert client._should_retry_pipe_error(object(), 0, 3) is False, (
        "an error without a winerror was retried"
    )
