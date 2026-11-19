"""Tests for atomic, durable fixture persistence."""

from __future__ import annotations

import json
import os
import stat
import typing as typ

import pytest

from cmd_mox.atomic_write import AtomicWriteError
from cmd_mox.record.fixture import FixtureFile
from cmd_mox.unittests._atomic_write_failures import install_replace_hook
from cmd_mox.unittests.test_fixture_file import _sample_fixture, _sample_metadata

if typ.TYPE_CHECKING:
    from pathlib import Path


class TestFixturePersistence:
    """Tests for atomic writes performed by ``FixtureFile.save``."""

    @pytest.mark.skipif(
        os.name == "nt", reason="POSIX file mode bits are not portable to Windows"
    )
    def test_save_restricts_fixture_permissions(self, tmp_path: Path) -> None:
        """A saved fixture is owner read/write only by default."""
        fixture_path = tmp_path / "fixture.json"

        _sample_fixture().save(fixture_path)

        assert stat.S_IMODE(fixture_path.stat().st_mode) == 0o600

    def test_save_round_trips_non_ascii_and_ends_with_a_newline(
        self, tmp_path: Path
    ) -> None:
        """The persisted fixture keeps non-ASCII text and a trailing newline."""
        fixture = _sample_fixture()
        fixture.recordings[0].stdout = "café\n"
        fixture_path = tmp_path / "fixture.json"

        fixture.save(fixture_path)

        # json.dumps escapes non-ASCII by default, so compare the decoded
        # document rather than the raw bytes.
        text = fixture_path.read_text(encoding="utf-8")
        assert text.endswith("}\n")
        assert json.loads(text)["recordings"][0]["stdout"] == "café\n"

    def test_save_failure_preserves_the_previous_fixture(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed replacement leaves the previous complete fixture in place."""
        fixture_path = tmp_path / "fixture.json"
        original = _sample_fixture()
        original.save(fixture_path)
        replacement = FixtureFile(
            version=FixtureFile.SCHEMA_VERSION,
            metadata=_sample_metadata(),
            recordings=[],
            scrubbing_rules=[],
        )

        def _fail_replace(_self: Path, _target: Path) -> Path:
            """Model a failure at the atomic replacement step."""
            message = "injected failure"
            raise OSError(message)

        # Fail inside the real helper so the test proves the helper itself
        # leaves the previous fixture recoverable.
        install_replace_hook(monkeypatch, _fail_replace)

        with pytest.raises(AtomicWriteError):
            replacement.save(fixture_path)

        loaded = FixtureFile.load(fixture_path)
        assert len(loaded.recordings) == 1
        assert loaded.metadata.created_at == original.metadata.created_at
