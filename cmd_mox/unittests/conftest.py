"""Shared fixtures for unit tests."""

from __future__ import annotations

import os
import socketserver
import subprocess
import typing as typ

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox.ipc import server as unix_server
from cmd_mox.ipc._unix_server_base import resolve_unix_server_base

if typ.TYPE_CHECKING:
    import collections.abc as cabc


class _StubUnixStreamServer(socketserver.BaseServer):
    """Stand-in Unix server base for socket-free backend tests."""


@pytest.fixture
def stub_unix_stream_server() -> type[socketserver.BaseServer]:
    """Provide the shared Unix server stub.

    Returns
    -------
    type[socketserver.BaseServer]
        The stand-in class used by socket-free backend tests.
    """
    return _StubUnixStreamServer


@pytest.fixture
def unsupported_unix_server_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch away both Unix server classes for unsupported-platform tests."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        socketserver,
        "UnixStreamServer",
        None,
        raising=False,
    )


@pytest.fixture
def native_unix_server_platform(
    monkeypatch: pytest.MonkeyPatch,
    stub_unix_stream_server: type[socketserver.BaseServer],
) -> None:
    """Select the stand-in native Unix server without opening sockets."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        socketserver,
        "ThreadingUnixStreamServer",
        stub_unix_stream_server,
        raising=False,
    )


def run_subprocess(
    args: cabc.Sequence[str],
    **kwargs: typ.Any,  # ruff: ignore[any-type] - forwarded verbatim to subprocess.run, whose keyword arguments are heterogeneous
) -> subprocess.CompletedProcess[str]:
    """Run ``subprocess.run`` with common defaults for tests.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process returned by ``subprocess.run``.
    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - the test executes a command path prepared by the test harness
        args, capture_output=True, text=True, check=True, **kwargs
    )


@pytest.fixture(name="run")
def run_fixture() -> cabc.Callable[..., subprocess.CompletedProcess[str]]:
    """Provide :func:`run_subprocess` as a fixture.

    Returns
    -------
    collections.abc.Callable
        The subprocess helper used by tests.
    """
    return run_subprocess


@pytest.fixture
def unix_server_base_cache() -> cabc.Iterator[None]:
    """Clear both lazy server caches around patched platform cases."""
    resolve_unix_server_base.cache_clear()
    unix_server._inner_server_cls.cache_clear()
    yield
    unix_server._inner_server_cls.cache_clear()
    resolve_unix_server_base.cache_clear()


@pytest.fixture
def stdin_pipe_descriptor() -> cabc.Iterator[int]:
    """Yield an owned pipe descriptor for stdin polling tests.

    Yields
    ------
    int
        The read end of the pipe.
    """
    read_descriptor, write_descriptor = os.pipe()
    try:
        yield read_descriptor
    finally:
        os.close(read_descriptor)
        os.close(write_descriptor)


@pytest.fixture
def stdin_pipe_descriptor_at_eof() -> cabc.Iterator[int]:
    """Yield a pipe read descriptor after closing its writer.

    Yields
    ------
    int
        The read end of the pipe, which returns EOF immediately.
    """
    read_descriptor, write_descriptor = os.pipe()
    os.close(write_descriptor)
    try:
        yield read_descriptor
    finally:
        os.close(read_descriptor)
