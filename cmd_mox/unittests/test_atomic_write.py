"""Unit tests for the atomic UTF-8 persistence helpers."""

from __future__ import annotations

import errno
import json
import os
import stat
import typing as typ

import pytest

from cmd_mox import atomic_write
from cmd_mox.atomic_write import (
    AtomicWriteError,
    atomic_write_json,
    atomic_write_text,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

_POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt", reason="POSIX file mode bits are not portable to Windows"
)


def _temporary_files(directory: Path) -> list[Path]:
    """Return the temporary files the helper may have left in *directory*."""
    return sorted(directory.glob(".*.tmp"))


def _raise_os_error(*_args: object, **_kwargs: object) -> None:
    """Model an operating-system failure raised by an injected seam."""
    message = "injected failure"
    raise OSError(errno.EIO, message)


def test_atomic_write_text_writes_utf8_content(tmp_path: Path) -> None:
    """Non-ASCII text is persisted and read back as UTF-8."""
    path = tmp_path / "note.txt"

    atomic_write_text(path, "café ☃\n")

    assert path.read_text(encoding="utf-8") == "café ☃\n"


def test_atomic_write_text_creates_parent_directories(tmp_path: Path) -> None:
    """Missing parent directories are created before the temporary file."""
    path = tmp_path / "nested" / "deeper" / "note.txt"

    atomic_write_text(path, "content\n")

    assert path.read_text(encoding="utf-8") == "content\n"


def test_newly_created_directories_are_synced_into_their_parents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each directory this call creates has its parent entry flushed."""
    synced: list[Path] = []
    monkeypatch.setattr(atomic_write, "_sync_parent_directory", synced.append)
    path = tmp_path / "new" / "deeper" / "note.txt"

    atomic_write_text(path, "content\n")

    # Innermost first: the file's directory, then each new ancestor's parent.
    assert synced == [tmp_path / "new" / "deeper", tmp_path / "new", tmp_path]


@_POSIX_ONLY
def test_temporary_name_fits_a_long_destination_name(tmp_path: Path) -> None:
    """A destination near the filename limit still gets a creatable temporary."""
    path = tmp_path / (("a" * 250) + ".json")

    atomic_write_text(path, "content\n")

    assert path.read_text(encoding="utf-8") == "content\n"


def test_atomic_write_text_overwrites_existing_file(tmp_path: Path) -> None:
    """A successful write replaces the previous contents."""
    path = tmp_path / "note.txt"
    path.write_text("old\n", encoding="utf-8")

    atomic_write_text(path, "new\n")

    assert path.read_text(encoding="utf-8") == "new\n"


def test_atomic_write_text_leaves_no_temporary_file(tmp_path: Path) -> None:
    """A successful write removes its sibling temporary file."""
    path = tmp_path / "note.txt"

    atomic_write_text(path, "content\n")

    assert _temporary_files(tmp_path) == []


@_POSIX_ONLY
def test_atomic_write_text_defaults_to_owner_only_mode(tmp_path: Path) -> None:
    """The persisted file is owner read/write only by default."""
    path = tmp_path / "note.txt"

    atomic_write_text(path, "content\n")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600


@_POSIX_ONLY
def test_atomic_write_text_honours_explicit_mode(tmp_path: Path) -> None:
    """An explicit mode replaces the restrictive default."""
    path = tmp_path / "note.txt"

    atomic_write_text(path, "content\n", mode=0o644)

    assert stat.S_IMODE(path.stat().st_mode) == 0o644


@_POSIX_ONLY
def test_atomic_write_text_replaces_symlink_not_its_target(tmp_path: Path) -> None:
    """The symbolic link entry is replaced and its old target is untouched."""
    real = tmp_path / "real.txt"
    real.write_text("original\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    link.symlink_to(real)

    atomic_write_text(link, "replacement\n")

    assert link.is_symlink() is False
    assert link.read_text(encoding="utf-8") == "replacement\n"
    assert real.read_text(encoding="utf-8") == "original\n"


def test_failed_replacement_preserves_previous_file_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replacement failure leaves the old file intact and removes the temporary."""
    path = tmp_path / "note.txt"
    path.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(atomic_write.Path, "replace", _raise_os_error)

    with pytest.raises(AtomicWriteError) as excinfo:
        atomic_write_text(path, "new\n")

    assert path.read_text(encoding="utf-8") == "old\n"
    assert _temporary_files(tmp_path) == []
    assert excinfo.value.path == path
    assert isinstance(excinfo.value.__cause__, OSError)


def test_file_sync_failure_preserves_previous_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure while syncing the temporary file leaves the old file intact."""
    path = tmp_path / "note.txt"
    path.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(atomic_write.os, "fsync", _raise_os_error)

    with pytest.raises(AtomicWriteError) as excinfo:
        atomic_write_text(path, "new\n")

    assert path.read_text(encoding="utf-8") == "old\n"
    assert _temporary_files(tmp_path) == []
    assert excinfo.value.path == path


def test_parent_directory_sync_failure_is_reported_after_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parent-directory sync failure reports uncertain durability."""
    path = tmp_path / "note.txt"
    monkeypatch.setattr(atomic_write, "_sync_parent_directory", _raise_os_error)

    with pytest.raises(AtomicWriteError) as excinfo:
        atomic_write_text(path, "new\n")

    # The replacement already succeeded, so the new contents are visible even
    # though the helper reports that durability is uncertain.
    assert path.read_text(encoding="utf-8") == "new\n"
    assert excinfo.value.path == path


def test_atomic_write_json_preserves_key_order_and_indentation(
    tmp_path: Path,
) -> None:
    """JSON output keeps insertion order and the standard indented layout."""
    path = tmp_path / "data.json"
    payload = {"second": 2, "first": 1}

    atomic_write_json(path, payload)

    text = path.read_text(encoding="utf-8")
    assert text == '{\n  "second": 2,\n  "first": 1\n}\n'
    assert json.loads(text) == payload


def test_atomic_write_json_wraps_serialization_failure(tmp_path: Path) -> None:
    """A non-serializable payload is reported as an atomic write error."""
    path = tmp_path / "data.json"

    with pytest.raises(AtomicWriteError) as excinfo:
        atomic_write_json(path, {"value": object()})

    assert excinfo.value.path == path
    assert isinstance(excinfo.value.__cause__, TypeError)


def test_atomic_write_error_is_an_os_error() -> None:
    """Callers may handle persistence failures through the OSError family."""
    assert issubclass(AtomicWriteError, OSError)
