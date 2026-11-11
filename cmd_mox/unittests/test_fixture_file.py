"""Unit tests for Record Mode fixture data models."""

from __future__ import annotations

import json
import re
import sys
import typing as typ

import hypothesis.strategies as st
import pytest
from hypothesis import given

from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation
from cmd_mox.record.scrubber import ScrubbingRule

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion

#: Every ``RecordedInvocation`` field is plain JSON-compatible data, so the
#: whole space can be generated from built-in strategies.
_RECORDED_INVOCATIONS = st.builds(
    RecordedInvocation,
    sequence=st.integers(),
    command=st.text(),
    args=st.lists(st.text(), max_size=4),
    stdin=st.text(),
    env_subset=st.dictionaries(st.text(), st.text(), max_size=4),
    stdout=st.text(),
    stderr=st.text(),
    exit_code=st.integers(),
    timestamp=st.text(),
    duration_ms=st.integers(),
)

#: JSON scalars that are never valid where the schema requires a string.
_NON_STRING_JSON_VALUES = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
)


def _sample_invocation(*, sequence: int = 0) -> RecordedInvocation:
    """Build a sample ``git status`` recording with the given sequence number."""
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
    """Return fixture metadata created with the default factory."""
    return FixtureMetadata.create()


def _sample_fixture(
    recordings: list[RecordedInvocation] | None = None,
) -> FixtureFile:
    """Build a fixture with sample metadata and recordings.

    Returns
    -------
    FixtureFile
        A fixture containing the supplied or default recordings.
    """
    return FixtureFile(
        version="1.0.0",
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


@given(_RECORDED_INVOCATIONS)
def test_recorded_invocation_survives_a_json_round_trip(
    original: RecordedInvocation,
) -> None:
    """Serializing to JSON text and back reconstructs an equal invocation.

    Invariant: ``from_dict(json.loads(json.dumps(x.to_dict()))) == x`` for any
    field values. This is precisely the contract ``FixtureFile.save`` and
    ``FixtureFile.load`` depend upon.
    """
    encoded = json.dumps(original.to_dict())
    rebuilt = RecordedInvocation.from_dict(json.loads(encoded))
    assert rebuilt == original, rebuilt


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


class TestFixtureFile:
    """Tests for FixtureFile serialization and persistence."""

    @pytest.mark.parametrize("source_version", ["1.0.0", "0.9"])
    def test_serialized_fixture_schema_snapshot(
        self, source_version: str, snapshot: SnapshotAssertion
    ) -> None:
        """Pin the complete canonical and migrated fixture mappings."""
        fixture = FixtureFile(
            version=source_version,
            metadata=FixtureMetadata(
                created_at="2025-01-15T10:30:00Z",
                cmdmox_version="0.2.0",
                platform="linux",
                python_version="3.12.0",
                test_module="tests/test_example.py",
                test_function="test_recording",
            ),
            recordings=[_sample_invocation()],
            scrubbing_rules=[ScrubbingRule(pattern="secret", replacement="[redacted]")],
        )
        result = FixtureFile.from_dict(fixture.to_dict())

        assert result.version == "1.0.0"
        assert result.to_dict() == snapshot

    def test_to_dict_schema_structure(self) -> None:
        """to_dict() produces the 1.0.0 schema structure."""
        fixture = _sample_fixture()
        d = fixture.to_dict()

        assert d["version"] == "1.0.0"
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
        assert raw["version"] == "1.0.0"

    def test_save_creates_parent_directories(self, tmp_path: Path) -> None:
        """save() creates intermediate directories when needed."""
        fixture = _sample_fixture()
        nested_path = tmp_path / "deep" / "nested" / "fixture.json"

        fixture.save(nested_path)

        assert nested_path.exists()
        loaded = FixtureFile.load(nested_path)
        assert loaded.version == "1.0.0"

    @pytest.mark.parametrize("fixture_version", ["0.9", "0.9.0"])
    def test_from_dict_migrates_old_version(self, fixture_version: str) -> None:
        """from_dict() migrates legacy fixtures to 1.0.0."""
        data = _sample_fixture().to_dict()
        data["version"] = fixture_version
        result = FixtureFile.from_dict(data)

        _assert_normalized_to_v1(result)

    @pytest.mark.parametrize("fixture_version", ["0.9", "0.9.0", None])
    def test_apply_migrations_stamps_old_version(
        self, fixture_version: str | None
    ) -> None:
        """Major-zero and unversioned fixtures retain data when migrated."""
        from cmd_mox.record.fixture import _apply_migrations

        data = _sample_fixture().to_dict()
        if fixture_version is None:
            del data["version"]
        else:
            data["version"] = fixture_version

        migrated = _apply_migrations(data)

        assert migrated == {**data, "version": "1.0.0"}

    def test_from_dict_migrates_when_version_missing(self) -> None:
        """from_dict() migrates a fixture without a version to 1.0.0."""
        data = _sample_fixture().to_dict()
        data.pop("version", None)
        result = FixtureFile.from_dict(data)

        _assert_normalized_to_v1(result)

    def test_from_dict_does_not_mutate_input(self) -> None:
        """from_dict() does not modify the caller's dict."""
        data = _sample_fixture().to_dict()
        data["version"] = "0.9.0"
        original_version = data["version"]
        FixtureFile.from_dict(data)

        assert data["version"] == original_version

    def test_from_dict_does_not_share_nested_references(self) -> None:
        """_apply_migrations deep-copies so nested objects are isolated."""
        from cmd_mox.record.fixture import _apply_migrations

        data = _sample_fixture().to_dict()
        data["version"] = "0.9.0"
        original_metadata = data["metadata"]
        result = _apply_migrations(data)

        # The nested metadata dict must be a distinct object, not shared.
        assert result["metadata"] is not original_metadata

    @pytest.mark.parametrize("fixture_version", ["1.1.0", "1.0.7"])
    def test_from_dict_tolerates_same_major_version_differences(
        self, fixture_version: str
    ) -> None:
        """from_dict() accepts same-major minor and patch differences."""
        data = _sample_fixture().to_dict()
        data["version"] = fixture_version
        data["new_future_field"] = "should be ignored"
        result = FixtureFile.from_dict(data)

        _assert_normalized_to_v1(result)

    def test_from_dict_rejects_incompatible_major_version(self) -> None:
        """from_dict() raises ValueError for a higher major with no migration."""
        data = _sample_fixture().to_dict()
        data["version"] = "99.0.0"
        with pytest.raises(ValueError, match=r"99\.0\.0"):
            FixtureFile.from_dict(data)

    def test_from_dict_normalizes_legacy_two_component_version(self) -> None:
        """Legacy two-component versions load and normalize to three parts."""
        from cmd_mox.record.fixture import _apply_migrations

        data = _sample_fixture().to_dict()
        data["version"] = "1.0"

        normalized = _apply_migrations(data)
        result = FixtureFile.from_dict(data)

        assert normalized["version"] == "1.0.0"
        assert result.version == "1.0.0"

    def test_from_dict_rejects_explicit_null_version(self) -> None:
        """from_dict() raises ValueError when version is explicitly None."""
        data = _sample_fixture().to_dict()
        data["version"] = None
        with pytest.raises(
            ValueError,
            match=r"^Invalid fixture schema: version: expected str, got NoneType$",
        ):
            FixtureFile.from_dict(data)

    def test_from_dict_normalizes_missing_metadata_to_value_error(self) -> None:
        """Missing required fields surface as a schema ValueError."""
        data = _sample_fixture().to_dict()
        del data["metadata"]
        with pytest.raises(
            ValueError,
            match=r"^Invalid fixture schema: missing required field 'metadata'$",
        ):
            FixtureFile.from_dict(data)

    def test_scrubbing_rules_serialization(self) -> None:
        """Scrubbing rules survive serialization."""
        rule = ScrubbingRule(
            pattern=r"ghp_\w+",
            replacement="<GITHUB_TOKEN>",
            applied_to=["env", "stdout"],
            description="GitHub PAT",
        )
        fixture = FixtureFile(
            version="1.0.0",
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


def _fixture_with_rule() -> dict[str, typ.Any]:
    """Return a sample fixture mapping that includes one scrubbing rule."""
    data = _sample_fixture().to_dict()
    data["scrubbing_rules"] = [
        ScrubbingRule(pattern="secret", replacement="[redacted]").to_dict()
    ]
    return data


_MISSING = object()


def _mutate_path(
    *path: str | int, value: object = _MISSING
) -> cabc.Callable[[dict[str, typ.Any]], None]:
    """Return a mutator that replaces or removes the value at *path*."""

    def mutate(data: dict[str, typ.Any]) -> None:
        """Apply the mutation to *data*."""
        target: typ.Any = data
        for key in path[:-1]:
            target = target[key]
        if value is _MISSING:
            del target[path[-1]]
        else:
            target[path[-1]] = value

    return mutate


_SCHEMA_REJECTION_CASES = [
    pytest.param(
        _mutate_path("recordings", 0, "stdout", value=None),
        r"recordings\[0\]\.stdout",
        id="null-stdout",
    ),
    pytest.param(
        _mutate_path("recordings", 0, "args", value="status"),
        r"recordings\[0\]\.args",
        id="string-args",
    ),
    pytest.param(
        _mutate_path("recordings", 0, "exit_code", value=1.9),
        r"recordings\[0\]\.exit_code",
        id="float-exit-code",
    ),
    pytest.param(
        _mutate_path("recordings", 0, "exit_code", value=True),
        r"recordings\[0\]\.exit_code",
        id="bool-exit-code",
    ),
    pytest.param(
        _mutate_path("recordings", 0, "args", value=["ok", 3]),
        r"recordings\[0\]\.args\[1\]",
        id="non-string-arg",
    ),
    pytest.param(
        _mutate_path("recordings", 0, "env_subset", value={"A": 1}),
        r"recordings\[0\]\.env_subset\['A'\]",
        id="non-string-env-value",
    ),
    pytest.param(
        _mutate_path("recordings", 0, "env_subset", value={1: "value"}),
        r"recordings\[0\]\.env_subset\[1\]",
        id="non-string-env-key",
    ),
    pytest.param(
        _mutate_path("recordings", 0, "env_subset", value=[]),
        r"recordings\[0\]\.env_subset",
        id="non-object-env-subset",
    ),
    pytest.param(
        _mutate_path("recordings", 0, "stdin"),
        r"recordings\[0\]\.stdin",
        id="missing-stdin",
    ),
    pytest.param(
        _mutate_path("recordings", 0, "stderr", value=None),
        r"recordings\[0\]\.stderr",
        id="null-stderr",
    ),
    pytest.param(
        _mutate_path("recordings", 0, "duration_ms", value=2.5),
        r"recordings\[0\]\.duration_ms",
        id="float-duration-ms",
    ),
    pytest.param(
        _mutate_path("metadata", "created_at"),
        r"missing required field 'metadata\.created_at'",
        id="missing-created-at",
    ),
    pytest.param(
        _mutate_path("metadata", "created_at", value=5),
        r"metadata\.created_at",
        id="non-string-created-at",
    ),
    pytest.param(
        _mutate_path("metadata", "test_module", value=42),
        r"metadata\.test_module",
        id="non-string-test-module",
    ),
    pytest.param(
        _mutate_path("metadata", "test_function", value=42),
        r"metadata\.test_function",
        id="non-string-test-function",
    ),
    pytest.param(
        _mutate_path("recordings"),
        r"missing required field 'recordings'",
        id="missing-recordings",
    ),
    pytest.param(
        _mutate_path("recordings", value=None),
        r"^Invalid fixture schema: recordings: expected list, got NoneType$",
        id="null-recordings",
    ),
    pytest.param(
        _mutate_path("recordings", value={"0": {}}),
        r"^Invalid fixture schema: recordings: expected list, got dict$",
        id="object-recordings",
    ),
    pytest.param(
        _mutate_path("recordings", value=["nope"]),
        r"recordings\[0\]",
        id="non-object-recording",
    ),
    pytest.param(
        _mutate_path("scrubbing_rules", 0, "pattern"),
        r"missing required field 'scrubbing_rules\[0\]\.pattern'",
        id="missing-pattern",
    ),
    pytest.param(
        _mutate_path("scrubbing_rules", 0, "pattern", value=3),
        r"scrubbing_rules\[0\]\.pattern",
        id="non-string-pattern",
    ),
    pytest.param(
        _mutate_path("scrubbing_rules", 0, "replacement"),
        r"missing required field 'scrubbing_rules\[0\]\.replacement'",
        id="missing-replacement",
    ),
    pytest.param(
        _mutate_path("scrubbing_rules", 0, "replacement", value=3),
        r"scrubbing_rules\[0\]\.replacement",
        id="non-string-replacement",
    ),
    pytest.param(
        _mutate_path("scrubbing_rules", 0, "applied_to", value="stdout"),
        r"scrubbing_rules\[0\]\.applied_to",
        id="string-applied-to",
    ),
    pytest.param(
        _mutate_path("scrubbing_rules", 0, "applied_to", value=["env", 2]),
        r"scrubbing_rules\[0\]\.applied_to\[1\]",
        id="non-string-applied-to",
    ),
    pytest.param(
        _mutate_path("scrubbing_rules", 0, "applied_to"),
        r"missing required field 'scrubbing_rules\[0\]\.applied_to'",
        id="missing-applied-to",
    ),
    pytest.param(
        _mutate_path("scrubbing_rules", 0, "description", value=3),
        r"scrubbing_rules\[0\]\.description",
        id="non-string-description",
    ),
    pytest.param(
        _mutate_path("scrubbing_rules"),
        r"missing required field 'scrubbing_rules'",
        id="missing-scrubbing-rules",
    ),
    pytest.param(
        _mutate_path("metadata", value=None),
        r"^Invalid fixture schema: metadata: expected object, got NoneType$",
        id="null-metadata",
    ),
    pytest.param(
        _mutate_path("metadata", value="nope"),
        r"^Invalid fixture schema: metadata: expected object, got str$",
        id="string-metadata",
    ),
]


class TestFixtureSchemaValidation:
    """Tests for strict 1.0.0 fixture schema validation."""

    @pytest.mark.parametrize(("mutate", "expected_path"), _SCHEMA_REJECTION_CASES)
    def test_rejects_malformed_field(
        self,
        mutate: cabc.Callable[[dict[str, typ.Any]], None],
        expected_path: str,
    ) -> None:
        """A malformed field raises a path-aware schema error."""
        data = _fixture_with_rule()
        mutate(data)

        with pytest.raises(ValueError, match=expected_path):
            FixtureFile.from_dict(data)

    def test_rejects_non_object_root(self) -> None:
        """A non-object root raises a schema error before migration."""
        with pytest.raises(ValueError, match=r"fixture: expected object, got list"):
            FixtureFile.from_dict(typ.cast("dict[str, typ.Any]", []))

    @pytest.mark.parametrize("key", ["test_module", "test_function"])
    def test_optional_metadata_keys_accept_missing_and_null(self, key: str) -> None:
        """Optional metadata keys accept both an absent key and an explicit null."""
        data = _sample_fixture().to_dict()
        data["metadata"].pop(key, None)
        assert getattr(FixtureFile.from_dict(data).metadata, key) is None

        data["metadata"][key] = None
        assert getattr(FixtureFile.from_dict(data).metadata, key) is None

    def test_missing_rule_description_defaults_to_empty_string(self) -> None:
        """A missing rule description becomes an empty string."""
        data = _fixture_with_rule()
        del data["scrubbing_rules"][0]["description"]

        result = FixtureFile.from_dict(data)

        assert result.scrubbing_rules[0].description == ""
        assert result.to_dict()["scrubbing_rules"][0]["description"] is None

    def test_null_rule_description_defaults_to_empty_string(self) -> None:
        """A null rule description becomes an empty string and reserializes as null."""
        data = _fixture_with_rule()
        data["scrubbing_rules"][0]["description"] = None

        result = FixtureFile.from_dict(data)

        assert result.scrubbing_rules[0].description == ""
        assert result.to_dict()["scrubbing_rules"][0]["description"] is None

    def test_unknown_nested_keys_are_ignored(self) -> None:
        """Unknown keys at every level are ignored rather than rejected."""
        data = _fixture_with_rule()
        data["future_root"] = {"value": 1}
        data["metadata"]["future_metadata"] = 1
        data["recordings"][0]["future_recording"] = [1, 2]
        data["scrubbing_rules"][0]["future_rule"] = None

        result = FixtureFile.from_dict(data)

        assert result.recordings[0].command == "git"
        assert result.scrubbing_rules[0].pattern == "secret"

    def test_load_rejects_reproduction_mapping(self, tmp_path: Path) -> None:
        """The audit reproduction mapping fails schema validation on load."""
        data = {
            "version": "1.0.0",
            "metadata": {
                "created_at": "2026-01-01T00:00:00+00:00",
                "cmdmox_version": "0.3.0",
                "platform": "linux",
                "python_version": "3.12",
            },
            "recordings": [
                {
                    "sequence": 0,
                    "command": "git",
                    "args": "status",
                    "stdin": None,
                    "env_subset": {},
                    "stdout": None,
                    "stderr": "",
                    "exit_code": 1.9,
                    "timestamp": "2026-01-01T00:00:00+00:00",
                    "duration_ms": 0,
                }
            ],
        }
        path = tmp_path / "reproduction.json"
        path.write_text(json.dumps(data))

        with pytest.raises(ValueError, match=r"recordings\[0\]\.args"):
            FixtureFile.load(path)

    def test_direct_from_dict_reports_bare_field_name(self) -> None:
        """A direct invocation parse reports the bare field name."""
        data = _sample_invocation().to_dict()
        data["stdout"] = None

        with pytest.raises(
            ValueError,
            match=r"^Invalid fixture schema: stdout: expected str, got NoneType$",
        ):
            RecordedInvocation.from_dict(data)

    def test_direct_from_dict_rejects_non_object(self) -> None:
        """A direct invocation parse rejects a non-object input."""
        with pytest.raises(
            ValueError,
            match=r"^Invalid fixture schema: expected object, got list$",
        ):
            RecordedInvocation.from_dict(typ.cast("dict[str, typ.Any]", []))


@given(
    data=st.data(),
    args=st.lists(st.text(), max_size=5),
    invalid=_NON_STRING_JSON_VALUES,
)
def test_args_reject_non_string_value_at_generated_position(
    data: st.DataObject,
    args: list[str],
    invalid: object,
) -> None:
    """A non-string value at any generated argument position names that index."""
    index = data.draw(st.integers(min_value=0, max_value=len(args)))
    mutated = [*args[:index], invalid, *args[index:]]
    fixture = _fixture_with_rule()
    fixture["recordings"][0]["args"] = mutated

    with pytest.raises(ValueError, match=re.escape(f"recordings[0].args[{index}]")):
        FixtureFile.from_dict(fixture)


@given(
    entries=st.dictionaries(st.text(), st.text(), max_size=4),
    key=st.integers(),
)
def test_env_subset_rejects_non_string_generated_key(
    entries: dict[str, str], key: int
) -> None:
    """A non-string key at any generated mapping position names that key."""
    fixture = _fixture_with_rule()
    fixture["recordings"][0]["env_subset"] = {**entries, key: "value"}

    with pytest.raises(
        ValueError, match=re.escape(f"recordings[0].env_subset[{key!r}]")
    ):
        FixtureFile.from_dict(fixture)


@given(
    entries=st.dictionaries(st.text(), st.text(), max_size=4),
    key=st.text(),
    invalid=_NON_STRING_JSON_VALUES,
)
def test_env_subset_rejects_non_string_generated_value(
    entries: dict[str, str], key: str, invalid: object
) -> None:
    """A non-string value at any generated mapping key names that key."""
    fixture = _fixture_with_rule()
    fixture["recordings"][0]["env_subset"] = {**entries, key: invalid}

    with pytest.raises(
        ValueError, match=re.escape(f"recordings[0].env_subset[{key!r}]")
    ):
        FixtureFile.from_dict(fixture)


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
        """Negative version components are rejected."""
        from cmd_mox.record.fixture import _parse_version

        with pytest.raises(ValueError, match="Invalid"):
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
def test_same_major_migration_preserves_mapping(minor: int, patch: int) -> None:
    """Same-major versions pass migration without discarding fields."""
    from cmd_mox.record.fixture import _apply_migrations

    data = {"version": f"1.{minor}.{patch}", "future_field": {"value": "kept"}}
    assert _apply_migrations(data) == data
