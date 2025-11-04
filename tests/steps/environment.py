"""pytest-bdd steps that manage environment and platform conditions."""

from __future__ import annotations

import collections
import os
import queue
import threading
import types
import typing as t

from pytest_bdd import given, parsers, then, when

import cmd_mox.environment as envmod
import cmd_mox.ipc.client as ipc_client
import cmd_mox.ipc.server as ipc_server
from cmd_mox.environment import CMOX_REAL_COMMAND_ENV_PREFIX

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    import pytest


@given("windows shim launchers are enabled")
def enable_windows_shims(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force shim generation to emit Windows batch launchers."""
    monkeypatch.setattr("cmd_mox.shimgen.IS_WINDOWS", True)


@given(parsers.cfparse('the platform override is "{platform}"'))
def set_platform_override(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    """Simulate running on an alternate platform such as Windows."""
    monkeypatch.setenv("CMD_MOX_PLATFORM_OVERRIDE", platform)


class _FakePipeHandle:
    def __init__(self, pipe: _FakePipe, role: str) -> None:
        self.pipe = pipe
        self.role = role


class _FakePipe:
    def __init__(self, name: str) -> None:
        self.name = name
        self.server_ready = threading.Event()
        self.client_ready = threading.Event()
        self.server_queue: queue.Queue[bytes | None] = queue.Queue()
        self.client_queue: queue.Queue[bytes | None] = queue.Queue()


class _FakePipeSystem:
    def __init__(self) -> None:
        self._pipes: dict[str, collections.deque[_FakePipe]] = collections.defaultdict(
            collections.deque
        )
        self._lock = threading.Lock()

    def create_server_pipe(self, name: str) -> _FakePipeHandle:
        pipe = _FakePipe(name)
        with self._lock:
            self._pipes[name].append(pipe)
        return _FakePipeHandle(pipe, "server")

    def connect_server(self, handle: _FakePipeHandle) -> None:
        handle.pipe.server_ready.set()
        handle.pipe.client_ready.wait()

    def wait_for_pipe(self, name: str, _timeout: int) -> bool:
        with self._lock:
            return bool(self._pipes.get(name))

    def connect_client(self, name: str) -> _FakePipeHandle:
        with self._lock:
            try:
                pipe = self._pipes[name].popleft()
            except IndexError as exc:
                raise FakePyWin.error(FakeWinError.ERROR_FILE_NOT_FOUND) from exc
        pipe.client_ready.set()
        pipe.server_ready.wait()
        return _FakePipeHandle(pipe, "client")

    def read(self, handle: _FakePipeHandle) -> tuple[int, bytes]:
        queue_obj = (
            handle.pipe.server_queue
            if handle.role == "server"
            else handle.pipe.client_queue
        )
        data = queue_obj.get()
        if data is None:
            raise FakePyWin.error(FakeWinError.ERROR_BROKEN_PIPE)
        return 0, data

    def write(self, handle: _FakePipeHandle, data: bytes) -> tuple[int, int]:
        target = (
            handle.pipe.client_queue
            if handle.role == "server"
            else handle.pipe.server_queue
        )
        target.put(bytes(data))
        return 0, len(data)

    def disconnect(self, handle: _FakePipeHandle) -> None:
        target = (
            handle.pipe.client_queue
            if handle.role == "server"
            else handle.pipe.server_queue
        )
        target.put(None)

    def close(self, handle: _FakePipeHandle) -> None:
        self.disconnect(handle)


class FakePyWinError(Exception):
    """Exception matching :mod:`pywintypes`' error type."""

    def __init__(self, winerror: int) -> None:
        super().__init__(f"error {winerror}")
        self.winerror = winerror


class FakePyWin:
    """Namespace exposing the ``error`` attribute."""

    error = FakePyWinError


class FakeWinError(types.SimpleNamespace):
    """Simple namespace exposing winerror constants."""

    ERROR_PIPE_BUSY = 231
    ERROR_FILE_NOT_FOUND = 2
    ERROR_BROKEN_PIPE = 109


class FakeWin32Pipe:
    """In-memory shim replicating the win32pipe module."""

    PIPE_ACCESS_DUPLEX = 0
    PIPE_TYPE_MESSAGE = 1
    PIPE_READMODE_MESSAGE = 2
    PIPE_WAIT = 0
    PIPE_UNLIMITED_INSTANCES = 255
    ERROR_MORE_DATA = 234
    ERROR_PIPE_CONNECTED = 535

    def __init__(self, system: _FakePipeSystem) -> None:
        self._system = system

    def CreateNamedPipe(self, name: str, *_args: object) -> _FakePipeHandle:  # noqa: N802,D102
        return self._system.create_server_pipe(name)

    def ConnectNamedPipe(self, handle: _FakePipeHandle, _overlapped: object) -> None:  # noqa: N802,D102
        self._system.connect_server(handle)

    def SetNamedPipeHandleState(self, *_args: object) -> None:  # noqa: N802,D102
        return None

    def DisconnectNamedPipe(self, handle: _FakePipeHandle) -> None:  # noqa: N802,D102
        self._system.disconnect(handle)

    def WaitNamedPipe(self, name: str, timeout: int) -> bool:  # noqa: N802,D102
        return self._system.wait_for_pipe(name, timeout)


class FakeWin32File:
    """In-memory shim replicating the win32file module."""

    GENERIC_READ = 1
    GENERIC_WRITE = 2
    OPEN_EXISTING = 3

    def __init__(self, system: _FakePipeSystem) -> None:
        self._system = system

    def CreateFile(self, name: str, *_args: object) -> _FakePipeHandle:  # noqa: N802,D102
        return self._system.connect_client(name)

    def ReadFile(self, handle: _FakePipeHandle, _size: int) -> tuple[int, bytes]:  # noqa: N802,D102
        return self._system.read(handle)

    def WriteFile(self, handle: _FakePipeHandle, data: bytes) -> tuple[int, int]:  # noqa: N802,D102
        return self._system.write(handle, data)

    def FlushFileBuffers(self, _handle: _FakePipeHandle) -> None:  # noqa: N802,D102
        return None

    def CloseHandle(self, handle: _FakePipeHandle) -> None:  # noqa: N802,D102
        self._system.close(handle)


@given("Windows IPC modules are simulated")
def simulate_windows_ipc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install in-memory Win32 IPC shims for behaviour tests."""
    system = _FakePipeSystem()
    fake_pipe = FakeWin32Pipe(system)
    fake_file = FakeWin32File(system)
    fake_winerror = FakeWinError()

    monkeypatch.setattr(envmod, "IS_WINDOWS", True)

    for module in (ipc_server, ipc_client):
        monkeypatch.setattr(module, "IS_WINDOWS", True)
        monkeypatch.setattr(module, "win32pipe", fake_pipe)
        monkeypatch.setattr(module, "win32file", fake_file)
        monkeypatch.setattr(module, "pywintypes", FakePyWin)
        monkeypatch.setattr(module, "winerror", fake_winerror)

    monkeypatch.setattr(
        ipc_server,
        "CallbackIPCServer",
        ipc_server._WindowsCallbackIPCServer,
    )


@given(parsers.cfparse('I set environment variable "{var}" to "{val}"'))
@when(parsers.cfparse('I set environment variable "{var}" to "{val}"'))
def set_env_var(monkeypatch: pytest.MonkeyPatch, var: str, val: str) -> None:
    """Adjust environment variable to new value (scoped to the test)."""
    monkeypatch.setenv(var, val)


@then(parsers.cfparse('PATHEXT should include "{extension}"'))
def pathext_should_include(extension: str) -> None:
    """Assert that PATHEXT contains *extension* (case-insensitive)."""
    pathext = os.environ.get("PATHEXT", "")
    entries = {
        item.strip().upper() for item in pathext.split(os.pathsep) if item.strip()
    }
    if extension.upper() not in entries:
        msg = f"PATHEXT {pathext!r} missing {extension}"
        raise AssertionError(msg)


@then(parsers.cfparse('PATHEXT should equal "{expected}"'))
def pathext_should_equal(expected: str) -> None:
    """Assert PATHEXT exactly matches *expected*."""
    value = os.environ.get("PATHEXT")
    if value != expected:
        msg = f"PATHEXT {value!r} != {expected!r}"
        raise AssertionError(msg)


@given(parsers.cfparse('the command "{cmd}" resolves to a non-executable file'))
def non_executable_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cmd: str,
) -> None:
    """Place a non-executable *cmd* earlier in ``PATH`` for passthrough tests."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    dummy = bin_dir / cmd
    dummy.write_text("#!/bin/sh\necho hi\n")
    dummy.chmod(0o644)

    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{original_path}")
    monkeypatch.setenv(f"{CMOX_REAL_COMMAND_ENV_PREFIX}{cmd}", str(dummy))


@given(parsers.cfparse('the command "{cmd}" will timeout'))
def command_will_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cmd: str,
) -> None:
    """Return a deterministic timeout-like response for *cmd*."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / cmd
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('timeout after 30 seconds\\n')\n"
        "sys.exit(124)\n"
    )
    script.chmod(0o755)

    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{original_path}")
    monkeypatch.setenv(f"{CMOX_REAL_COMMAND_ENV_PREFIX}{cmd}", str(script))
