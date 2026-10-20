"""Shared fixtures for system-level tests."""

from __future__ import annotations

import pathlib
import tempfile
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

import pytest


@pytest.fixture
def short_unix_socket_path() -> cabc.Iterator[Path]:
    """Yield a short Unix socket path and clean up its temporary directory.

    Yields
    ------
    pathlib.Path
        Short socket path kept alive for one test.
    """
    with tempfile.TemporaryDirectory(prefix="cmox-") as short_dir:
        yield pathlib.Path(short_dir) / "ipc.sock"
