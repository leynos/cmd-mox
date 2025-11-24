"""Unit tests for shared path normalization helpers."""

from __future__ import annotations

import ntpath
import typing as t

import cmd_mox._path_utils as path_utils

if t.TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_normalize_path_string_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    """POSIX normalisation should collapse redundant separators."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    assert path_utils.normalize_path_string("/opt/../bin//") == "/bin"


def test_normalize_path_string_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows normalisation should apply normpath and normcase."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    raw = r"C:\Tools\..\BIN"
    expected = ntpath.normcase(ntpath.normpath(raw))
    assert path_utils.normalize_path_string(raw) == expected


def test_normalize_path_accepts_pathlike(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """PathLike inputs should normalise via ``os.fspath``."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    base = tmp_path / "cmdmox"
    path = (base / "nested") / ".." / "final"
    assert path_utils.normalize_path(path) == str(base / "final")
