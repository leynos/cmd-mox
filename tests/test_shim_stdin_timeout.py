"""Behavioural tests for bounded stdin capture in a real shim process.

The guarantee under test is about process lifetime: a stdin read that never
reaches EOF must not keep the shim alive past ``CMOX_IPC_TIMEOUT``, even though
the stream's owner still holds the write end open. An in-process test cannot
observe that, because the shim deliberately leaves forced termination to its
script entry point. The module therefore runs the generated launcher the way a
user would, and it is part of ``windows-smoke`` so the native Windows handle
strategy is exercised against a real pipe handle.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import threading
import typing as typ

from cmd_mox import create_shim_symlinks
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, CMOX_IPC_TIMEOUT_ENV
from cmd_mox.ipc import IPCHandlers, IPCServer, NamedPipeServer, Response

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from cmd_mox.ipc import Invocation

_COMMAND = "bounded-shim"
_SHIM_TIMEOUT = 0.25
# Deliberately far above ``_SHIM_TIMEOUT``. The assertion is that the shim exits
# at all while its stdin writer is still open, not that it exits exactly on the
# deadline, so only a genuinely hung process should trip this bound.
_REAP_TIMEOUT = 10.0
# Reached as soon as the shim's write ends close. The join is only a guard for a
# child that leaked an inherited handle, which ``_REAP_TIMEOUT`` also catches;
# keeping both under the suite's 30-second test timeout matters more than their
# exact values.
_DRAIN_TIMEOUT = 5.0


def _install_shim(tmp_path: Path) -> Path:
    """Create a shim launcher for :data:`_COMMAND` in *tmp_path*.

    Returns
    -------
    pathlib.Path
        The POSIX symlink or Windows ``.cmd`` launcher created for the command.
    """
    shim_dir = tmp_path / "shims"
    shim_dir.mkdir()
    return create_shim_symlinks(shim_dir, [_COMMAND])[_COMMAND]


def _shim_arguments(shim_path: Path) -> list[str]:
    """Return the argv that runs *shim_path* in a child process.

    The launcher is started by path with no shell, which is how a user's
    ``PATH`` lookup ends up invoking it, so the test also covers the Windows
    batch launcher's exit-status forwarding.

    Returns
    -------
    list[str]
        Argv for :class:`subprocess.Popen`.
    """
    return [str(shim_path)]


def _shim_environment(tmp_path: Path, timeout: float) -> dict[str, str]:
    """Build a child environment carrying the shim's IPC settings.

    Returns
    -------
    dict[str, str]
        A copy of the process environment plus the socket and timeout settings.
    """
    return os.environ | {
        CMOX_IPC_SOCKET_ENV: str(tmp_path / "ipc.sock"),
        CMOX_IPC_TIMEOUT_ENV: f"{timeout:g}",
    }


def _drain(stream: typ.BinaryIO, sink: list[bytes]) -> None:
    """Copy *stream* into *sink* until EOF.

    Reading continuously is what keeps a full pipe from blocking the shim while
    the test waits for it to exit.
    """
    while chunk := stream.read(64 * 1024):
        sink.append(chunk)


def test_stalled_stdin_exits_while_the_writer_stays_open(tmp_path: Path) -> None:
    """A stdin pipe held open past the deadline ends the shim with status 1."""
    stdout: list[bytes] = []
    stderr: list[bytes] = []
    with subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] - argv names a test-created shim
        _shim_arguments(_install_shim(tmp_path)),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_shim_environment(tmp_path, _SHIM_TIMEOUT),
    ) as process:
        readers = [
            threading.Thread(target=_drain, args=(stream, sink), daemon=True)
            for stream, sink in ((process.stdout, stdout), (process.stderr, stderr))
        ]
        try:
            for reader in readers:
                reader.start()
            # The write end stays open throughout, so the shim can only finish
            # by giving up on its read. ``communicate`` would close that end
            # for us and quietly turn this into an ordinary EOF test.
            process.wait(timeout=_REAP_TIMEOUT)
            for reader in readers:
                reader.join(timeout=_DRAIN_TIMEOUT)
            assert not any(reader.is_alive() for reader in readers), (
                "stdout/stderr did not reach EOF while stdin's writer was open"
            )
        finally:
            # ``Popen.__exit__`` waits without a bound, so a shim that ignored
            # its deadline must be killed before the context manager unwinds.
            if process.poll() is None:
                process.kill()
                process.wait(timeout=_REAP_TIMEOUT)
            for reader in readers:
                reader.join(timeout=_DRAIN_TIMEOUT)

    assert process.returncode == 1, (
        f"a stalled stdin read must exit 1, got {process.returncode}"
    )
    assert b"".join(stdout) == b"", "stdin expiry must not write response stdout"
    assert b"".join(stderr).decode() == (
        f"IPC error: stdin read timed out after {_SHIM_TIMEOUT:g} seconds\n"
    ), "the shim did not report the bounded stdin diagnostic"


@contextlib.contextmanager
def _serving(tmp_path: Path, received: list[Invocation]) -> cabc.Iterator[None]:
    """Serve IPC for one shim run, answering with a distinctive exit code.

    Yields
    ------
    None
        Control while the server is accepting connections.
    """
    handlers = IPCHandlers(
        handler=lambda invocation: (
            received.append(invocation) or Response(stdout="forwarded\n", exit_code=3)
        )
    )
    if os.name == "nt":
        server: IPCServer | NamedPipeServer = NamedPipeServer(
            tmp_path / "ipc.sock", handlers=handlers
        )
    else:
        server = IPCServer(tmp_path / "ipc.sock", handlers=handlers)
    with server:
        yield


def test_closed_stdin_completes_and_forwarding_is_unchanged(tmp_path: Path) -> None:
    """A closed stdin still reaches IPC, and its exit status is not remapped."""
    received: list[Invocation] = []
    shim_path = _install_shim(tmp_path)

    with _serving(tmp_path, received):
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - argv names a test-created shim
            _shim_arguments(shim_path),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            env=_shim_environment(tmp_path, 5.0),
            timeout=_REAP_TIMEOUT,
            check=False,
        )

    assert result.returncode == 3, (
        "the configured command exit code must survive the bounded capture, "
        f"stderr: {result.stderr.decode()}"
    )
    assert result.stdout == b"forwarded\n", "the shim did not copy response stdout"
    assert result.stderr == b"", "a completed capture must not report a diagnostic"
    assert [invocation.stdin for invocation in received] == [""], (
        "a closed stdin must dispatch the same empty body as before"
    )
