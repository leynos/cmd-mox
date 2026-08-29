"""Cross-platform unit tests for the Windows named-pipe transport.

The transport only runs on Windows, so these tests substitute fakes for the
``pywin32`` modules and force :data:`cmd_mox._path_utils.IS_WINDOWS` to ``True``
where the production code branches on the platform.
"""

from __future__ import annotations

import dataclasses as dc
import threading
import time
import typing as typ

import pytest

from cmd_mox.ipc import TimeoutConfig
from cmd_mox.ipc._named_pipe_limits import join_threads_before, remaining_seconds
from cmd_mox.ipc.models import Invocation, PassthroughResult, Response
from cmd_mox.ipc.named_pipe import (
    CallbackNamedPipeServer,
    NamedPipeServer,
    _NamedPipeState,
)
from cmd_mox.ipc.windows import (
    ERROR_BROKEN_PIPE,
    ERROR_FILE_NOT_FOUND,
    ERROR_NO_DATA,
    ERROR_OPERATION_ABORTED,
    ERROR_PIPE_BUSY,
    ERROR_PIPE_CONNECTED,
    derive_pipe_name,
)
from cmd_mox.unittests._named_pipe_fakes import (  # ruff: ignore[unused-import] - re-exported pytest fixtures
    UNEXPECTED_WINERROR,
    FakePyWinTypes,
    FakeWin32File,
    FakeWin32Pipe,
    FakeWinError,
    PatchWin32,
    build_state,
    closed_pipe_handles,
    patch_win32_fixture,
    windows_platform_fixture,
)

if typ.TYPE_CHECKING:
    import pathlib


def _finished_thread() -> threading.Thread:
    """Return a thread that has already run to completion.

    Returns
    -------
    threading.Thread
        A started and joined thread.
    """
    thread = threading.Thread(target=lambda: None)
    thread.start()
    thread.join()
    return thread


def _echo(invocation: Invocation) -> Response:
    """Return the command name as stdout.

    Returns
    -------
    Response
        A response echoing the invoked command.
    """
    return Response(stdout=invocation.command)


def _ack(_result: PassthroughResult) -> Response:
    """Acknowledge a passthrough result.

    Returns
    -------
    Response
        A fixed acknowledgement response.
    """
    return Response(stdout="ack")


def test_named_pipe_server_requires_windows(tmp_path: pathlib.Path) -> None:
    """Constructing the server on POSIX should fail fast."""
    with pytest.raises(RuntimeError, match="only available on Windows"):
        NamedPipeServer(tmp_path / "ipc.sock")


@pytest.mark.usefixtures("windows_platform")
def test_named_pipe_server_derives_pipe_name(tmp_path: pathlib.Path) -> None:
    """The server derives its pipe name from the socket path."""
    server = NamedPipeServer(tmp_path / "ipc.sock", timeout=1.0)

    assert server._pipe_name == derive_pipe_name(server.socket_path), "name mismatch"
    assert server._prepare_backend_start() is None, "start hook should be inert"


@pytest.mark.usefixtures("windows_platform")
def test_create_backend_builds_unstarted_daemon_thread(tmp_path: pathlib.Path) -> None:
    """Backend creation returns a state and an unstarted daemon thread."""
    server = NamedPipeServer(tmp_path / "ipc.sock", timeout=1.0, accept_timeout=0.5)

    state, thread = server._create_backend()

    assert state.pipe_name == server._pipe_name, "state uses a different pipe name"
    assert state.outer is server, "state does not reference its server"
    assert state.accept_timeout == pytest.approx(0.5), "accept timeout not forwarded"
    assert thread.daemon, "backend thread should be a daemon"
    assert not thread.is_alive(), "backend thread should not be started yet"


@pytest.mark.usefixtures("windows_platform")
def test_wait_until_ready_tolerates_missing_backend(tmp_path: pathlib.Path) -> None:
    """Waiting without a backend is a no-op."""
    server = NamedPipeServer(tmp_path / "ipc.sock", timeout=1.0)

    assert server._wait_until_ready() is None, "missing backend should be tolerated"


@pytest.mark.usefixtures("windows_platform")
def test_wait_until_ready_returns_when_state_is_ready(tmp_path: pathlib.Path) -> None:
    """A pre-signalled state satisfies the readiness wait immediately."""
    server = NamedPipeServer(tmp_path / "ipc.sock", timeout=1.0)
    state = build_state()
    state.ready_event.set()
    server._server = state

    assert server._wait_until_ready() is None, "ready state should not raise"


@pytest.mark.usefixtures("windows_platform")
def test_wait_until_ready_raises_when_state_never_signals(
    tmp_path: pathlib.Path, patch_win32: PatchWin32
) -> None:
    """A backend that never signals readiness is stopped and reported."""
    patch_win32(FakeWin32File(FakeWinError(ERROR_FILE_NOT_FOUND)))
    server = NamedPipeServer(tmp_path / "ipc.sock", timeout=0.01)
    state = build_state()
    server._server = state

    with pytest.raises(RuntimeError, match="not accepting connections"):
        server._wait_until_ready()

    assert state.stop_event.is_set(), "the backend should be stopped on timeout"


@pytest.mark.usefixtures("windows_platform")
def test_stop_backend_tolerates_missing_server(tmp_path: pathlib.Path) -> None:
    """Stopping a server that never started is a no-op."""
    server = NamedPipeServer(tmp_path / "ipc.sock", timeout=1.0)

    assert server._stop_backend(None) is None, "missing backend should be tolerated"


@pytest.mark.usefixtures("windows_platform")
def test_stop_backend_stops_state_and_joins_clients(
    tmp_path: pathlib.Path, patch_win32: PatchWin32
) -> None:
    """Stopping the backend halts the accept loop and joins client threads."""
    patch_win32(FakeWin32File(FakeWinError(ERROR_FILE_NOT_FOUND)))
    server = NamedPipeServer(tmp_path / "ipc.sock", timeout=1.0)
    state = build_state()

    server._stop_backend(state)

    assert state.stop_event.is_set(), "stop event was not set"
    assert state.ready_event.is_set(), "ready event was not released"


@pytest.mark.usefixtures("windows_platform")
@pytest.mark.parametrize(
    "timeouts",
    [None, TimeoutConfig(timeout=2.0, accept_timeout=0.25)],
    ids=["default-timeouts", "explicit-timeouts"],
)
def test_callback_named_pipe_server_wires_handlers(
    tmp_path: pathlib.Path, timeouts: TimeoutConfig | None
) -> None:
    """The callback variant forwards handlers and timeout configuration."""
    expected = timeouts or TimeoutConfig()
    server = CallbackNamedPipeServer(
        tmp_path / "ipc.sock", _echo, _ack, timeouts=timeouts
    )

    invocation = Invocation(command="whoami", args=[], stdin="", env={})
    result = PassthroughResult(invocation_id="id", stdout="", stderr="", exit_code=0)
    assert server.handle_invocation(invocation).stdout == "whoami", "handler not wired"
    assert server.handle_passthrough_result(result).stdout == "ack", "no passthrough"
    assert server.timeout == expected.timeout, "timeout was not forwarded"


def test_try_connect_pipe_reports_success(patch_win32: PatchWin32) -> None:
    """A successful connection keeps serving with a connected handle."""
    _file_fake, pipe_fake = patch_win32()
    handle = object()

    assert build_state()._try_connect_pipe(handle) == (True, True), "bad decision"
    assert pipe_fake.connect_calls == [handle], "handle was not connected"


@pytest.mark.parametrize(
    ("winerror", "expected", "expect_closed"),
    [
        (ERROR_PIPE_CONNECTED, (True, True), False),
        (ERROR_OPERATION_ABORTED, (False, False), True),
        (ERROR_NO_DATA, (False, False), True),
        (UNEXPECTED_WINERROR, (True, False), True),
        (None, (True, False), True),
    ],
    ids=["already-connected", "aborted", "no-data", "unexpected", "no-winerror"],
)
def test_try_connect_pipe_maps_errors(
    patch_win32: PatchWin32,
    winerror: int | None,
    expected: tuple[bool, bool],
    expect_closed: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - parametrized expectation, not an API flag
) -> None:
    """Connection failures map to accept-loop control-flow decisions."""
    file_fake, _pipe_fake = patch_win32(
        win32pipe=FakeWin32Pipe(connect_results=[FakeWinError(winerror)])
    )
    handle = object()

    assert build_state()._try_connect_pipe(handle) == expected, "bad decision"
    closed = closed_pipe_handles(file_fake)
    assert (closed == [handle]) is expect_closed, "handle disposal mismatch"


def test_close_handle_delegates_to_win32file(patch_win32: PatchWin32) -> None:
    """Closing a handle calls straight through to ``win32file``."""
    file_fake, _pipe_fake = patch_win32()
    handle = object()

    _NamedPipeState._close_handle(handle)

    assert file_fake.closed == [handle], "handle was not closed"


def test_spawn_handler_thread_tracks_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Spawned handler threads are tracked while they run."""
    state = build_state()
    seen: list[object] = []
    started = threading.Event()

    def handler(handle: object, _slot: object = None) -> None:
        seen.append(handle)
        started.set()

    monkeypatch.setattr(state, "_handle_client", handler)
    handle = object()
    state._spawn_handler_thread(handle)

    assert started.wait(2.0), "handler thread never ran"
    assert seen == [handle], "handler received the wrong handle"
    assert len(state._get_active_threads()) == 1, "thread was not tracked"


def test_get_active_threads_returns_a_snapshot() -> None:
    """The active-thread accessor returns a copy of the tracked set."""
    state = build_state()
    thread = _finished_thread()
    state._client_threads.add(thread)

    snapshot = state._get_active_threads()
    snapshot.clear()

    assert state._get_active_threads() == [thread], "snapshot aliased internal state"


@pytest.mark.parametrize(
    ("offset", "is_expired"),
    [(-1.0, True), (0.0, True), (5.0, False)],
    ids=["past", "now", "future"],
)
def test_remaining_seconds(
    offset: float,
    is_expired: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - parametrized expectation, not an API flag
) -> None:
    """Remaining time is ``None`` once the deadline has passed."""
    remaining = remaining_seconds(time.monotonic() + offset)

    assert (remaining is None) is is_expired, "expiry classification mismatch"


@pytest.mark.parametrize(
    ("offset", "thread_count", "expected"),
    [(5.0, 0, True), (5.0, 1, True), (5.0, 2, True), (-1.0, 1, False)],
    ids=["no-threads", "one-joined", "all-joined", "deadline-passed"],
)
def test_join_threads_before(
    offset: float,
    thread_count: int,
    expected: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - parametrized expectation, not an API flag
) -> None:
    """All threads are joined unless the deadline expires part-way."""
    threads = [_finished_thread() for _ in range(thread_count)]

    result = join_threads_before(threads, time.monotonic() + offset)

    assert result is expected, "join completion decision mismatch"


def test_join_clients_returns_without_threads() -> None:
    """Joining clients returns immediately when none are tracked."""
    assert build_state().join_clients(5.0) is None, "join should return promptly"


def test_join_clients_returns_when_deadline_already_passed() -> None:
    """A non-positive timeout abandons the join without waiting."""
    state = build_state()
    state._client_threads.add(_finished_thread())

    assert state.join_clients(0.0) is None, "join should abandon on expiry"
    assert state._get_active_threads(), "tracked threads should remain"


def test_join_clients_gives_up_when_a_thread_outlives_the_deadline() -> None:
    """A slow client thread exhausts the budget and the join is abandoned."""
    state = build_state()
    release = threading.Event()
    # Two blocked threads guarantee the first join exhausts the whole budget,
    # so the second join is abandoned rather than merely timing out.
    blockers = [threading.Thread(target=release.wait, daemon=True) for _ in range(2)]
    for blocker in blockers:
        blocker.start()
    state._client_threads.update(blockers)

    try:
        state.join_clients(0.05)
    finally:
        release.set()
        for blocker in blockers:
            blocker.join(5.0)

    assert len(state._get_active_threads()) == 2, "threads should remain tracked"


def test_join_clients_waits_for_threads_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The join loop exits once handler threads deregister themselves."""
    state = build_state()

    def handler(_handle: object, _slot: object = None) -> None:
        with state._client_lock:
            state._client_threads.discard(threading.current_thread())

    monkeypatch.setattr(state, "_handle_client", handler)
    state._spawn_handler_thread(object())

    state.join_clients(5.0)

    assert not state._get_active_threads(), "client threads were not drained"


@pytest.mark.usefixtures("windows_platform")
def test_serve_forever_spawns_handler_until_stopped(
    patch_win32: PatchWin32, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connected client is handed to a handler thread before shutdown."""
    handle = object()
    patch_win32(win32pipe=FakeWin32Pipe(handles=[handle]))
    state = build_state()
    spawned: list[object] = []

    def fake_spawn(client_handle: object, _slot: object = None) -> None:
        spawned.append(client_handle)
        state.stop_event.set()

    monkeypatch.setattr(state, "_spawn_handler_thread", fake_spawn)
    state.serve_forever()

    assert spawned == [handle], "the client was not dispatched to a handler"
    assert state.ready_event.is_set(), "readiness was never signalled"


@pytest.mark.usefixtures("windows_platform")
def test_serve_forever_closes_handle_when_stopped_after_connect(
    patch_win32: PatchWin32,
) -> None:
    """A shutdown racing the connection closes the freshly accepted handle."""
    handle = object()
    state = build_state()
    file_fake, _pipe_fake = patch_win32(
        win32pipe=FakeWin32Pipe(
            handles=[handle], connect_results=[state.stop_event.set]
        )
    )

    state.serve_forever()

    assert closed_pipe_handles(file_fake) == [handle], "accepted handle not closed"


@pytest.mark.usefixtures("windows_platform")
def test_serve_forever_stops_when_connect_is_aborted(
    patch_win32: PatchWin32,
) -> None:
    """An aborted connection ends the accept loop."""
    handle = object()
    file_fake, pipe_fake = patch_win32(
        win32pipe=FakeWin32Pipe(
            handles=[handle],
            connect_results=[FakeWinError(ERROR_OPERATION_ABORTED)],
        )
    )
    state = build_state()

    state.serve_forever()

    assert closed_pipe_handles(file_fake) == [handle], "aborted handle not closed"
    assert len(pipe_fake.create_calls) == 1, "the loop should not accept again"


@pytest.mark.usefixtures("windows_platform")
def test_serve_forever_continues_after_unexpected_connect_error(
    patch_win32: PatchWin32, caplog: pytest.LogCaptureFixture
) -> None:
    """An unexpected connect error is logged and the loop accepts again."""
    caplog.set_level("ERROR", logger="cmd_mox.ipc.named_pipe")
    _file_fake, pipe_fake = patch_win32(
        win32pipe=FakeWin32Pipe(
            handles=[object(), FakeWinError(UNEXPECTED_WINERROR)],
            connect_results=[FakeWinError(UNEXPECTED_WINERROR)],
        )
    )
    state = build_state()

    state.serve_forever()

    assert len(pipe_fake.create_calls) == 2, "the loop did not retry the accept"
    assert "Named pipe connect failed" in caplog.text, "connect failure not logged"
    assert "Named pipe accept failed" in caplog.text, "accept failure not logged"


def test_stop_is_idempotent(patch_win32: PatchWin32) -> None:
    """Repeated stop requests poke the pipe only once."""
    file_fake, _pipe_fake = patch_win32(
        FakeWin32File(FakeWinError(ERROR_FILE_NOT_FOUND))
    )
    state = build_state()

    state.stop()
    state.stop()

    assert len(file_fake.create_file_calls) == 1, "the pipe was poked twice"
    assert state.ready_event.is_set(), "waiters were not released"


def test_poke_pipe_closes_the_wakeup_handle(patch_win32: PatchWin32) -> None:
    """A successful wakeup connection is closed immediately."""
    handle = object()
    file_fake, _pipe_fake = patch_win32(FakeWin32File(handle))

    build_state()._poke_pipe()

    assert file_fake.closed == [handle], "the wakeup handle leaked"


@pytest.mark.parametrize(
    ("winerror", "is_logged"),
    [
        (ERROR_FILE_NOT_FOUND, False),
        (ERROR_PIPE_BUSY, False),
        (UNEXPECTED_WINERROR, True),
    ],
    ids=["not-found", "busy", "unexpected"],
)
def test_poke_pipe_handles_connection_errors(
    patch_win32: PatchWin32,
    caplog: pytest.LogCaptureFixture,
    winerror: int,
    is_logged: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - parametrized expectation, not an API flag
) -> None:
    """Expected wakeup failures stay silent; unexpected ones are logged."""
    caplog.set_level("DEBUG", logger="cmd_mox.ipc.named_pipe")
    file_fake, _pipe_fake = patch_win32(FakeWin32File(FakeWinError(winerror)))

    build_state()._poke_pipe()

    assert ("wakeup failed" in caplog.text) is is_logged, "logging mismatch"
    assert not file_fake.closed, "no handle exists to close"


@dc.dataclass(slots=True, frozen=True)
class _HandleClientFailureCase:
    """Windows error and logging expectation for one client failure."""

    winerror: int
    is_logged: bool


@pytest.mark.parametrize(
    "case",
    [
        pytest.param(
            _HandleClientFailureCase(ERROR_BROKEN_PIPE, is_logged=False),
            id="broken-pipe",
        ),
        pytest.param(
            _HandleClientFailureCase(ERROR_NO_DATA, is_logged=False),
            id="no-data",
        ),
        pytest.param(
            _HandleClientFailureCase(UNEXPECTED_WINERROR, is_logged=True),
            id="unexpected",
        ),
    ],
)
def test_handle_client_reports_unexpected_failures(
    patch_win32: PatchWin32,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    case: _HandleClientFailureCase,
) -> None:
    """Disconnect-style read failures stay quiet; others are logged."""
    caplog.set_level("ERROR", logger="cmd_mox.ipc.named_pipe")
    file_fake, pipe_fake = patch_win32()
    state = build_state()

    def fail_read(_handle: object) -> bytes:
        raise FakeWinError(case.winerror)

    monkeypatch.setattr(state, "_read_request", fail_read)
    handle = object()
    state._handle_client(handle)

    assert ("handler failed" in caplog.text) is case.is_logged, "logging mismatch"
    assert file_fake.closed == [handle], "the client handle was not closed"
    assert pipe_fake.disconnected == [handle], "the client was not disconnected"


def test_handle_client_returns_when_request_is_missing(
    patch_win32: PatchWin32, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing request short-circuits before the pipeline runs."""
    file_fake, _pipe_fake = patch_win32()
    state = build_state()
    monkeypatch.setattr(state, "_read_request", lambda _handle: None)
    handle = object()
    state._client_threads.add(threading.current_thread())

    state._handle_client(handle)

    assert file_fake.closed == [handle], "the client handle was not closed"
    assert not state._get_active_threads(), "the client thread was not untracked"


def test_handle_client_suppresses_disconnect_failures(
    patch_win32: PatchWin32, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing disconnect still leaves the handle closed."""
    file_fake, _pipe_fake = patch_win32(
        win32pipe=FakeWin32Pipe(disconnect_error=FakeWinError(UNEXPECTED_WINERROR))
    )
    state = build_state()
    monkeypatch.setattr(state, "_read_request", lambda _handle: None)
    handle = object()

    state._handle_client(handle)

    assert file_fake.closed == [handle], "the client handle was not closed"


@pytest.mark.parametrize(
    ("accept_timeout", "expected_ms"),
    [(0.25, 250), (2.0, 2000), (0.0001, 1)],
    ids=["sub-second", "multi-second", "rounds-up-to-one"],
)
def test_create_pipe_instance_forwards_configuration(
    patch_win32: PatchWin32, accept_timeout: float, expected_ms: int
) -> None:
    """Pipe creation passes the derived name, modes, and timeout."""
    handle = object()
    _file_fake, pipe_fake = patch_win32(win32pipe=FakeWin32Pipe(handles=[handle]))
    state = build_state(accept_timeout=accept_timeout)

    created = state._create_pipe_instance()

    call = pipe_fake.create_calls[0]
    assert created is handle, "the created handle was not returned"
    assert call[0] == "pipe", "the pipe name was not forwarded"
    expected_access = (
        FakeWin32Pipe.PIPE_ACCESS_DUPLEX | FakeWin32File.FILE_FLAG_OVERLAPPED
    )
    assert call[1] == expected_access, "wrong access mode"
    assert call[6] == expected_ms, "the accept timeout was not converted to ms"
