"""Unit tests for fixture schema version parsing and migration."""

from __future__ import annotations

import copy
import json
import typing as typ

import hypothesis.strategies as st
import pytest
from hypothesis import given

from cmd_mox.record.fixture import FixtureFile
from cmd_mox.unittests._fixture_helpers import fixture_with_rule, sample_fixture

if typ.TYPE_CHECKING:
    from pathlib import Path


def _assert_normalized_to_v1(result: FixtureFile) -> None:
    """Assert *result* carries schema 1.0.0 with its recording intact.

    Parameters
    ----------
    result : FixtureFile
        The fixture returned by ``FixtureFile.from_dict``.
    """
    assert result.version == "1.0.0", result.version
    assert len(result.recordings) == 1, result.recordings
    assert result.recordings[0].command == "git", result.recordings[0]


class TestFixtureMigration:
    """Tests for legacy version handling through the public fixture APIs."""

    @pytest.mark.parametrize("fixture_version", ["0.9", "0.9.0", None])
    def test_migration_preserves_legacy_fields(
        self, fixture_version: str | None, tmp_path: Path
    ) -> None:
        """Legacy and unversioned fixtures migrate with their data intact."""
        data = sample_fixture().to_dict()
        if fixture_version is None:
            del data["version"]
        else:
            data["version"] = fixture_version
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps(data))

        results = (FixtureFile.from_dict(data), FixtureFile.load(path))

        for result in results:
            _assert_normalized_to_v1(result)
            assert result.metadata.created_at == data["metadata"]["created_at"]
            assert result.recordings[0].stdout == data["recordings"][0]["stdout"]

    def test_from_dict_does_not_mutate_input(self) -> None:
        """from_dict() does not modify the caller's dict."""
        data = sample_fixture().to_dict()
        data["version"] = "0.9.0"
        original_version = data["version"]
        FixtureFile.from_dict(data)

        assert data["version"] == original_version

    def test_migration_receives_a_deep_copy(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A nested mutation by a migration leaves the caller's mapping intact."""
        from cmd_mox.record import fixture as fixture_module

        def stamp_platform(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
            """Mutate nested metadata to prove the input was copied."""
            data["metadata"]["platform"] = "migrated"
            return data

        monkeypatch.setattr(
            fixture_module, "_MIGRATIONS", {0: ((1, 0, 0), stamp_platform)}
        )
        data = fixture_with_rule()
        data["version"] = "0.9.0"
        original_metadata = copy.deepcopy(data["metadata"])

        result = FixtureFile.from_dict(data)

        assert data["metadata"] == original_metadata
        assert result.metadata.platform == "migrated"

    @pytest.mark.parametrize("fixture_version", ["1.1.0", "1.0.7"])
    def test_from_dict_tolerates_same_major_version_differences(
        self, fixture_version: str
    ) -> None:
        """from_dict() accepts same-major minor and patch differences."""
        data = sample_fixture().to_dict()
        data["version"] = fixture_version
        data["new_future_field"] = "should be ignored"
        result = FixtureFile.from_dict(data)

        _assert_normalized_to_v1(result)
        assert "new_future_field" not in result.to_dict()

    def test_from_dict_rejects_incompatible_major_version(self) -> None:
        """from_dict() raises ValueError for a higher major with no migration."""
        data = sample_fixture().to_dict()
        data["version"] = "99.0.0"
        with pytest.raises(
            ValueError,
            match=(
                r"Unsupported fixture schema version '99\.0\.0'; "
                r"no migration path to '1\.0\.0'"
            ),
        ):
            FixtureFile.from_dict(data)

    @pytest.mark.parametrize(
        "legacy_version",
        ["1.0", "01.0"],
        ids=["two-component", "leading-zero"],
    )
    def test_legacy_version_loads_through_both_apis(
        self, legacy_version: str, tmp_path: Path
    ) -> None:
        """Legacy two-component versions normalize through from_dict and load."""
        data = sample_fixture().to_dict()
        data["version"] = legacy_version
        path = tmp_path / "legacy.json"
        path.write_text(json.dumps(data))

        results = (FixtureFile.from_dict(data), FixtureFile.load(path))

        for result in results:
            _assert_normalized_to_v1(result)

    @pytest.mark.parametrize(
        "bad_version",
        # The escaped value spells one and zero with Arabic-Indic digits.
        ["0_1.0", "+1.0", "\u0661.\u0660", "1. 2", "-0.0", "1.-1"],
        ids=[
            "underscore-between-digits",
            "explicit-positive-sign",
            "non-ascii-decimal-digits",
            "internal-whitespace",
            "negative-major",
            "negative-minor",
        ],
    )
    def test_from_dict_rejects_non_ascii_legacy_components(
        self, bad_version: str
    ) -> None:
        """Legacy components accept only ASCII digits before integer parsing."""
        data = sample_fixture().to_dict()
        data["version"] = bad_version

        with pytest.raises(ValueError, match=r"Invalid schema version"):
            FixtureFile.from_dict(data)

    def test_load_rejects_non_ascii_legacy_components(self, tmp_path: Path) -> None:
        """A malformed legacy version fails through the file loader too."""
        data = sample_fixture().to_dict()
        data["version"] = "0_1.0"
        path = tmp_path / "malformed.json"
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match=r"Invalid schema version"):
            FixtureFile.load(path)

    def test_from_dict_rejects_explicit_null_version(self) -> None:
        """from_dict() raises ValueError when version is explicitly None."""
        data = sample_fixture().to_dict()
        data["version"] = None
        with pytest.raises(
            ValueError,
            match=r"^Invalid fixture schema: version: expected str, got NoneType$",
        ):
            FixtureFile.from_dict(data)


class TestFixtureMigrationChain:
    """Tests for the chained fixture migration registry."""

    def test_migration_chain_hops_through_future_majors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A fixture crosses every registered hop and each hop sees its version."""
        from cmd_mox.record import fixture as fixture_module

        received: list[tuple[str, str]] = []

        def migrate_one(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
            """Record the received version and mark the first hop."""
            received.append(("first", data["version"]))
            data["metadata"]["platform"] = "first"
            return data

        def migrate_two(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
            """Record the received version and mark the second hop."""
            received.append(("second", data["version"]))
            data["metadata"]["python_version"] = "second"
            return data

        monkeypatch.setattr(fixture_module, "_CURRENT_VERSION", (2, 0, 0))
        monkeypatch.setattr(FixtureFile, "SCHEMA_VERSION", "2.0.0")
        monkeypatch.setattr(
            fixture_module,
            "_MIGRATIONS",
            {0: ((1, 0, 0), migrate_one), 1: ((2, 0, 0), migrate_two)},
        )
        data = fixture_with_rule()
        data["version"] = "0.9.0"
        original = copy.deepcopy(data)

        result = FixtureFile.from_dict(data)

        assert received == [("first", "0.9.0"), ("second", "1.0.0")]
        assert result.version == "2.0.0"
        assert result.metadata.platform == "first"
        assert result.metadata.python_version == "second"
        assert result.recordings[0].command == "git"
        assert data == original

        reparsed = FixtureFile.from_dict(result.to_dict())

        assert reparsed.version == "2.0.0"
        assert received == [("first", "0.9.0"), ("second", "1.0.0")]

    def test_builtin_registry_migrates_through_a_future_hop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fixed 0 -> 1 target still runs a newly added 1 -> 2 hop."""
        from cmd_mox.record import fixture as fixture_module

        received: list[tuple[str, str]] = []

        def migrate_one_to_two(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
            """Record the version stamped by the built-in migration."""
            received.append(("second", data["version"]))
            data["metadata"]["python_version"] = "second"
            return data

        assert fixture_module._MIGRATIONS[0][0] == (1, 0, 0)
        monkeypatch.setattr(fixture_module, "_CURRENT_VERSION", (2, 0, 0))
        monkeypatch.setattr(FixtureFile, "SCHEMA_VERSION", "2.0.0")
        monkeypatch.setattr(
            fixture_module,
            "_MIGRATIONS",
            {**fixture_module._MIGRATIONS, 1: ((2, 0, 0), migrate_one_to_two)},
        )
        data = fixture_with_rule()
        data["version"] = "0.9.0"

        result = FixtureFile.from_dict(data)

        assert received == [("second", "1.0.0")]
        assert result.version == "2.0.0"
        assert result.metadata.python_version == "second"
        assert result.recordings[0].command == "git"

    def test_migration_chain_reports_missing_path_for_empty_registry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty registry reports the missing path from the file version."""
        from cmd_mox.record import fixture as fixture_module

        monkeypatch.setattr(fixture_module, "_CURRENT_VERSION", (2, 0, 0))
        monkeypatch.setattr(fixture_module, "_MIGRATIONS", {})
        data = fixture_with_rule()
        data["version"] = "0.9.0"

        with pytest.raises(
            ValueError,
            match=r"No migration path from schema version '0\.9\.0' to '2\.0\.0'",
        ):
            FixtureFile.from_dict(data)

    def test_migration_chain_reports_missing_intermediate_hop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A chain that stops at major 1 reports the version it stamped."""
        from cmd_mox.record import fixture as fixture_module

        def migrate_one(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
            """Pass the mapping through unchanged to leave major 2 unregistered."""
            return data

        monkeypatch.setattr(fixture_module, "_CURRENT_VERSION", (2, 0, 0))
        monkeypatch.setattr(
            fixture_module, "_MIGRATIONS", {0: ((1, 0, 0), migrate_one)}
        )
        data = fixture_with_rule()
        data["version"] = "0.9.0"

        with pytest.raises(
            ValueError,
            match=r"No migration path from schema version '1\.0\.0' to '2\.0\.0'",
        ):
            FixtureFile.from_dict(data)

    def test_migration_chain_rejects_non_advancing_target(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A target that keeps the source major is rejected before it runs."""
        from cmd_mox.record import fixture as fixture_module

        calls: list[str] = []

        def must_not_run(data: dict[str, typ.Any]) -> dict[str, typ.Any]:
            """Record an unexpected call so the test can assert it never ran."""
            calls.append(data["version"])
            return data

        monkeypatch.setattr(
            fixture_module, "_MIGRATIONS", {0: ((0, 1, 0), must_not_run)}
        )
        data = fixture_with_rule()
        data["version"] = "0.9.0"

        with pytest.raises(ValueError, match=r"does not advance the major version"):
            FixtureFile.from_dict(data)

        assert calls == []


class TestVersionParsing:
    """Tests for the _parse_version helper."""

    @pytest.mark.parametrize(
        ("version_str", "parts"),
        [(" 1.2.3 ", ["1", "2", "3"]), ("01.0", ["01", "0"])],
    )
    def test_split_version_preserves_components(
        self, version_str: str, parts: list[str]
    ) -> None:
        """Syntax validation preserves components for numeric parsing."""
        from cmd_mox.record.fixture import _split_version

        assert _split_version(version_str) == parts

    def test_split_version_rejects_noncanonical_three_part_version(self) -> None:
        """Three-part version components cannot have leading zeroes."""
        from cmd_mox.record.fixture import _split_version

        with pytest.raises(ValueError, match="Invalid"):
            _split_version("1.01.0")

    @pytest.mark.parametrize(
        ("version_str", "expected"),
        [
            ("1.0.0", (1, 0, 0)),
            (" 1.0.0 ", (1, 0, 0)),
            ("1.1.0", (1, 1, 0)),
            ("0.9.0", (0, 9, 0)),
            ("1.2.3", (1, 2, 3)),
            ("1.0", (1, 0, 0)),
            ("01.0", (1, 0, 0)),
        ],
        ids=[
            "simple",
            "surrounding-whitespace-stripped",
            "non-zero-minor",
            "zero-major",
            "non-zero-patch",
            "legacy-two-component",
            "legacy-leading-zero",
        ],
    )
    def test_parse_valid_versions(
        self, version_str: str, expected: tuple[int, int, int]
    ) -> None:
        """Well-formed version strings parse into comparable integer tuples."""
        from cmd_mox.record.fixture import _parse_version

        assert _parse_version(version_str) == expected

    def test_parse_negative_component_raises(self) -> None:
        """Negative legacy version components are rejected."""
        from cmd_mox.record.fixture import _parse_version

        with pytest.raises(ValueError, match=r"Invalid schema version"):
            _parse_version("1.-1")

    @pytest.mark.parametrize(
        "bad_version",
        [
            "1",
            "1.a",
            "abc",
            "1.2.3.4",
            "1.-0.0",
            "1.+1.0",
            "1.01.0",
            "1_0.0.0",
            "\uff11.0.0",
        ],
    )
    def test_parse_rejects_invalid_formats(self, bad_version: str) -> None:
        """Various malformed version strings are rejected."""
        from cmd_mox.record.fixture import _parse_version

        with pytest.raises(ValueError, match="Invalid"):
            _parse_version(bad_version)


@given(
    major=st.integers(min_value=0, max_value=10**12),
    minor=st.integers(min_value=0, max_value=10**12),
    patch=st.integers(min_value=0, max_value=10**12),
)
def test_canonical_and_legacy_versions_round_trip(
    major: int, minor: int, patch: int
) -> None:
    """Canonical and legacy numeric components retain their values."""
    from cmd_mox.record.fixture import _parse_version

    assert _parse_version(f"{major}.{minor}.{patch}") == (major, minor, patch)
    assert _parse_version(f"{major}.{minor}") == (major, minor, 0)


@given(component=st.integers(min_value=0, max_value=10**12))
def test_canonical_version_rejects_leading_zero(component: int) -> None:
    """A numeric component with a leading zero is not canonical SemVer."""
    from cmd_mox.record.fixture import _parse_version

    with pytest.raises(ValueError, match="Invalid"):
        _parse_version(f"1.0.0{component}")


@given(
    minor=st.integers(min_value=0, max_value=10**12),
    patch=st.integers(min_value=0, max_value=10**12),
)
def test_same_major_version_preserves_known_fields(minor: int, patch: int) -> None:
    """Same-major versions load without migration and discard unknown fields."""
    data = sample_fixture().to_dict()
    data["version"] = f"1.{minor}.{patch}"
    data["future_field"] = {"value": "discarded"}

    result = FixtureFile.from_dict(data)

    assert result.version == "1.0.0"
    assert result.recordings[0].command == "git"
    assert "future_field" not in result.to_dict()
