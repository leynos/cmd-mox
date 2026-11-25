"""Unit tests for atomic fixture saves."""

from __future__ import annotations

import errno
import importlib
import json
import os
import stat
import sys
import typing as typ

import hypothesis.strategies as st
import pytest
from hypothesis import example, given

from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation

if typ.TYPE_CHECKING:
    from pathlib import Path


def _invocation(
    *, command: str = "git", args: list[str] | None = None, stdout: str = ""
) -> RecordedInvocation:
    """Build one recording with the supplied text fields."""
    return RecordedInvocation(
        sequence=0,
        command=command,
        args=[] if args is None else args,
        stdin="",
        env_subset={"LANG": "en_GB.UTF-8"},
        stdout=stdout,
        stderr="",
        exit_code=0,
        timestamp="2025-01-15T10:30:01Z",
        duration_ms=1,
    )


def _fixture(recordings: list[RecordedInvocation]) -> FixtureFile:
    """Build a fixture around *recordings* with generated metadata."""
    return FixtureFile(
        version=FixtureFile.SCHEMA_VERSION,
        metadata=FixtureMetadata.create(),
        recordings=recordings,
        scrubbing_rules=[],
    )


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not enforced"
)
def test_save_writes_owner_only_fixture(tmp_path: Path) -> None:
    """A saved fixture is readable and writable only by its owner."""
    fixture_path = tmp_path / "owner_only.json"
    previous_umask = os.umask(0o022)
    try:
        _fixture([_invocation()]).save(fixture_path)
    finally:
        os.umask(previous_umask)

    assert stat.S_IMODE(fixture_path.stat().st_mode) == 0o600


def test_save_failure_preserves_previous_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed save leaves the previous fixture loadable and byte-identical."""
    module = importlib.import_module("cmd_mox._atomic_write")
    fixture_path = tmp_path / "fixture.json"
    _fixture([_invocation()]).save(fixture_path)
    before = fixture_path.read_bytes()

    def refuse_replace(temporary: Path, target: Path) -> None:
        """Report that the target cannot be replaced."""
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(module, "_replace", refuse_replace)

    with pytest.raises(module.AtomicWriteError) as excinfo:
        _fixture([_invocation(), _invocation()]).save(fixture_path)

    assert excinfo.value.stage is module.AtomicWriteStage.REPLACE
    assert fixture_path.read_bytes() == before
    assert len(FixtureFile.load(fixture_path).recordings) == 1


@given(command=st.text(), args=st.lists(st.text(), max_size=4), stdout=st.text())
@example(command="café", args=["héllo", "😀"], stdout="héllo\n")
def test_serialization_matches_legacy_fixture_bytes(
    command: str, args: list[str], stdout: str
) -> None:
    """The helper writes exactly the bytes FixtureFile.save used to write."""
    module = importlib.import_module("cmd_mox._atomic_write")
    payload = _fixture([
        _invocation(command=command, args=args, stdout=stdout)
    ]).to_dict()

    expected = (json.dumps(payload, indent=2) + "\n").encode()

    assert module._serialize_json(payload) == expected
