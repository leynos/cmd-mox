"""Unit tests for Record Mode fixture data models."""

from __future__ import annotations

import json
import sys
import typing as t

from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation
from cmd_mox.record.scrubber import ScrubbingRule

if t.TYPE_CHECKING:
    from pathlib import Path


def _sample_invocation(*, sequence: int = 0) -> RecordedInvocation:
    return RecordedInvocation(
        sequence=sequence,
        command="git",
        args=["status", "--short"],
        stdin="",
        env_subset={"GIT_AUTHOR_NAME": "Test User"},
        stdout="M file.py\n",
        stderr="",
        exit_code=0,
        timestamp="2025-01-15T10:30:01Z",
        duration_ms=42,
    )


def _sample_metadata() -> FixtureMetadata:
    return FixtureMetadata.create()


def _sample_fixture(
    recordings: list[RecordedInvocation] | None = None,
) -> FixtureFile:
    return FixtureFile(
        version="1.0",
        metadata=_sample_metadata(),
        recordings=recordings or [_sample_invocation()],
        scrubbing_rules=[],
    )


class TestRecordedInvocation:
    """Tests for RecordedInvocation serialization."""

    def test_to_dict_roundtrip(self) -> None:
        """RecordedInvocation survives a to_dict/from_dict cycle."""
        original = _sample_invocation()
        rebuilt = RecordedInvocation.from_dict(original.to_dict())

        assert rebuilt.sequence == original.sequence
        assert rebuilt.command == original.command
        assert rebuilt.args == original.args
        assert rebuilt.stdin == original.stdin
        assert rebuilt.env_subset == original.env_subset
        assert rebuilt.stdout == original.stdout
        assert rebuilt.stderr == original.stderr
        assert rebuilt.exit_code == original.exit_code
        assert rebuilt.timestamp == original.timestamp
        assert rebuilt.duration_ms == original.duration_ms

    def test_to_dict_field_names(self) -> None:
        """to_dict produces the schema-specified field names."""
        d = _sample_invocation().to_dict()
        expected_keys = {
            "sequence",
            "command",
            "args",
            "stdin",
            "env_subset",
            "stdout",
            "stderr",
            "exit_code",
            "timestamp",
            "duration_ms",
        }
        assert set(d.keys()) == expected_keys


class TestFixtureMetadata:
    """Tests for FixtureMetadata creation and serialization."""

    def test_create_captures_platform_info(self) -> None:
        """FixtureMetadata.create() auto-populates platform details."""
        meta = FixtureMetadata.create()

        assert meta.platform == sys.platform
        assert meta.python_version == sys.version
        assert meta.created_at  # non-empty ISO8601 string
        assert meta.cmdmox_version  # non-empty version string

    def test_create_with_test_context(self) -> None:
        """FixtureMetadata.create() accepts optional test context."""
        meta = FixtureMetadata.create(
            test_module="tests/test_example.py",
            test_function="test_something",
        )
        assert meta.test_module == "tests/test_example.py"
        assert meta.test_function == "test_something"

    def test_to_dict_roundtrip(self) -> None:
        """FixtureMetadata survives a to_dict/from_dict cycle."""
        original = FixtureMetadata.create(
            test_module="mod.py",
            test_function="test_fn",
        )
        rebuilt = FixtureMetadata.from_dict(original.to_dict())

        assert rebuilt.created_at == original.created_at
        assert rebuilt.cmdmox_version == original.cmdmox_version
        assert rebuilt.platform == original.platform
        assert rebuilt.python_version == original.python_version
        assert rebuilt.test_module == original.test_module
        assert rebuilt.test_function == original.test_function


class TestFixtureFile:
    """Tests for FixtureFile serialization and persistence."""

    def test_to_dict_schema_structure(self) -> None:
        """to_dict() produces the v1.0 schema structure."""
        fixture = _sample_fixture()
        d = fixture.to_dict()

        assert d["version"] == "1.0"
        assert "metadata" in d
        assert "recordings" in d
        assert "scrubbing_rules" in d
        assert isinstance(d["recordings"], list)
        assert len(d["recordings"]) == 1
        assert isinstance(d["scrubbing_rules"], list)

    def test_from_dict_roundtrip(self) -> None:
        """FixtureFile survives a to_dict/from_dict cycle."""
        original = _sample_fixture()
        rebuilt = FixtureFile.from_dict(original.to_dict())

        assert rebuilt.version == original.version
        assert rebuilt.metadata.created_at == original.metadata.created_at
        assert len(rebuilt.recordings) == len(original.recordings)
        assert rebuilt.recordings[0].command == original.recordings[0].command
        assert rebuilt.recordings[0].args == original.recordings[0].args

    def test_save_and_load(self, tmp_path: Path) -> None:
        """Fixture saved to disk loads back identically."""
        fixture = _sample_fixture()
        fixture_path = tmp_path / "test_fixture.json"

        fixture.save(fixture_path)

        assert fixture_path.exists()
        loaded = FixtureFile.load(fixture_path)

        assert loaded.version == fixture.version
        assert loaded.metadata.platform == fixture.metadata.platform
        assert len(loaded.recordings) == 1
        assert loaded.recordings[0].command == "git"

        # Verify it is valid JSON
        raw = json.loads(fixture_path.read_text())
        assert raw["version"] == "1.0"

    def test_save_creates_parent_directories(self, tmp_path: Path) -> None:
        """save() creates intermediate directories when needed."""
        fixture = _sample_fixture()
        nested_path = tmp_path / "deep" / "nested" / "fixture.json"

        fixture.save(nested_path)

        assert nested_path.exists()
        loaded = FixtureFile.load(nested_path)
        assert loaded.version == "1.0"

    def test_scrubbing_rules_serialization(self) -> None:
        """Scrubbing rules survive serialization."""
        rule = ScrubbingRule(
            pattern=r"ghp_\w+",
            replacement="<GITHUB_TOKEN>",
            applied_to=["env", "stdout"],
            description="GitHub PAT",
        )
        fixture = FixtureFile(
            version="1.0",
            metadata=_sample_metadata(),
            recordings=[],
            scrubbing_rules=[rule],
        )
        rebuilt = FixtureFile.from_dict(fixture.to_dict())

        assert len(rebuilt.scrubbing_rules) == 1
        assert rebuilt.scrubbing_rules[0].pattern == r"ghp_\w+"
        assert rebuilt.scrubbing_rules[0].replacement == "<GITHUB_TOKEN>"
        assert rebuilt.scrubbing_rules[0].applied_to == ["env", "stdout"]
        assert rebuilt.scrubbing_rules[0].description == "GitHub PAT"
