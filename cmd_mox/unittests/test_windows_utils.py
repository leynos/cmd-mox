"""Cross-platform unit tests for Windows IPC helpers."""

from __future__ import annotations

import pathlib

from cmd_mox.ipc.windows import derive_pipe_name


def test_derive_pipe_name_is_deterministic(tmp_path: pathlib.Path) -> None:
    """Hashing the same identifier should always return the same pipe name."""
    identifier = pathlib.Path(tmp_path) / "shim" / "ipc.sock"
    first = derive_pipe_name(identifier)
    second = derive_pipe_name(identifier)
    assert first == second


def test_derive_pipe_name_varies_per_identifier(tmp_path: pathlib.Path) -> None:
    """Different identifiers should map to different pipe names."""
    first = derive_pipe_name(pathlib.Path(tmp_path) / "one.sock")
    second = derive_pipe_name(pathlib.Path(tmp_path) / "two.sock")
    assert first != second
