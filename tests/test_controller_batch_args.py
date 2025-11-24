"""Unit tests for Windows batch argument escaping helper."""

from __future__ import annotations

import typing as t

from tests.helpers import controller

if t.TYPE_CHECKING:
    import pytest


def _set_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force helpers to behave as if running on Windows."""
    monkeypatch.setattr(controller.os, "name", "nt")


def _set_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force helpers to behave as if running on POSIX."""
    monkeypatch.setattr(controller.os, "name", "posix")


def test_escape_batch_args_is_noop_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-Windows platforms should return argv unchanged."""
    _set_posix(monkeypatch)
    argv = ["build.cmd", "arg^1"]
    assert controller.escape_windows_batch_args(argv) == argv


def test_escape_batch_args_for_cmd_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    """Arguments to explicit .cmd scripts should have carets quadrupled."""
    _set_windows(monkeypatch)
    argv = ["build.cmd", "arg^1", "safe"]

    escaped = controller.escape_windows_batch_args(argv)

    assert escaped == ["build.cmd", "arg^^^^1", "safe"]


def test_escape_batch_args_resolves_pathext(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolution through PATHEXT should still trigger caret escaping."""
    _set_windows(monkeypatch)
    monkeypatch.setattr(controller.shutil, "which", lambda cmd: f"C:/tools/{cmd}.cmd")
    argv = ["builder", "^caret"]

    escaped = controller.escape_windows_batch_args(argv)

    assert escaped == ["builder", "^^^^caret"]


def test_escape_batch_args_missing_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing PATH resolution should leave arguments unchanged."""
    _set_windows(monkeypatch)
    monkeypatch.setattr(controller.shutil, "which", lambda cmd: None)
    argv = ["builder", "^caret"]

    escaped = controller.escape_windows_batch_args(argv)

    assert escaped == ["builder", "^caret"]


def test_escape_batch_args_ignores_non_batch_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Executables should not be treated as batch scripts."""
    _set_windows(monkeypatch)
    monkeypatch.setattr(controller.shutil, "which", lambda cmd: f"C:/bin/{cmd}.exe")
    argv = ["builder", "^"]

    escaped = controller.escape_windows_batch_args(argv)

    assert escaped == argv


def test_escape_batch_args_preserves_percent_and_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Percent signs and empty arguments should be left untouched."""
    _set_windows(monkeypatch)
    argv = ["build.cmd", "%PATH%", ""]

    escaped = controller.escape_windows_batch_args(argv)

    assert escaped == ["build.cmd", "%PATH%", ""]
