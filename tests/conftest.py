"""Fixtures used by system-level tests.

Use ``short_unix_socket_path`` for tests that bind AF_UNIX sockets, for example
``def test_server(short_unix_socket_path: Path)``. The fixture keeps socket
names below the platform's path-length limit.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

import pytest

_MAX_UNIX_SOCKET_PATH_BYTES = 100


def _short_socket_temp_root() -> pathlib.Path:
    """Find a writable temporary root that leaves room for an AF_UNIX name.

    Returns
    -------
    pathlib.Path
        A writable directory where generated socket paths stay below the
        platform limit.

    Raises
    ------
    RuntimeError
        No writable short directory is available.
    """
    roots = (pathlib.Path(tempfile.gettempdir()), pathlib.Path.home())
    for root in roots:
        for candidate in (root, *root.parents):
            socket_path = candidate / "cmox-xxxxxxxx" / "ipc.sock"
            if len(os.fsencode(socket_path)) > _MAX_UNIX_SOCKET_PATH_BYTES:
                continue
            if not candidate.is_dir():
                continue
            if not os.access(candidate, os.W_OK | os.X_OK):
                continue
            return candidate
    raise RuntimeError


@pytest.fixture
def short_unix_socket_path() -> cabc.Iterator[Path]:
    """Yield a short Unix socket path and clean up its temporary directory.

    Yields
    ------
    pathlib.Path
        Short socket path kept alive for one test.
    """
    short_root = _short_socket_temp_root() if os.name == "posix" else None
    with tempfile.TemporaryDirectory(prefix="cmox-", dir=short_root) as short_dir:
        yield pathlib.Path(short_dir) / "ipc.sock"
