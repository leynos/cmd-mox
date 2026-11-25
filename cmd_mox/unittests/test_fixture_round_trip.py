"""Round-trip properties for fixture persistence.

Every fixture the writer can emit must survive ``to_dict()``/``from_dict()`` and
``save()``/``load()`` unchanged. The reachability checks keep the generated
domain honest, and the legacy golden fixture pins backward compatibility with
the two-component schema version.
"""

from __future__ import annotations

import tempfile
import typing as typ
from pathlib import Path

import hypothesis
import pytest
from hypothesis import given, settings

from cmd_mox.record import fixture as fixture_module
from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation
from cmd_mox.record.scrubber import ScrubbingRule
from cmd_mox.unittests._replay_helpers import fixture_files

if typ.TYPE_CHECKING:
    import collections.abc as cabc

#: Fixture written with the legacy two-component schema version.
GOLDEN_FIXTURE_PATH = Path(__file__).with_name("data") / "replay_golden_v1.json"

_ROUND_TRIP_LABELS = (
    "non-ascii text",
    "empty recordings",
    "env present",
    "null description",
)


def _fixture_texts(fixture: FixtureFile) -> list[str]:
    """Collect every string value stored in *fixture*."""
    texts = [
        fixture.metadata.created_at,
        fixture.metadata.cmdmox_version,
        fixture.metadata.platform,
        fixture.metadata.python_version,
    ]
    texts.extend(
        optional
        for optional in (fixture.metadata.test_module, fixture.metadata.test_function)
        if optional is not None
    )
    for recording in fixture.recordings:
        texts.extend([
            recording.command,
            recording.stdin,
            recording.stdout,
            recording.stderr,
            recording.timestamp,
            *recording.args,
            *recording.env_subset.keys(),
            *recording.env_subset.values(),
        ])
    for rule in fixture.scrubbing_rules:
        texts.extend([
            rule.pattern,
            rule.replacement,
            rule.description,
            *rule.applied_to,
        ])
    return texts


def classify_fixture(fixture: FixtureFile) -> frozenset[str]:
    """Label which LOAD-1 coverage classes *fixture* belongs to.

    Parameters
    ----------
    fixture : FixtureFile
        The generated fixture to label.

    Returns
    -------
    frozenset of str
        Every applicable label from ``_ROUND_TRIP_LABELS``.
    """
    labels = set()
    if any(not text.isascii() for text in _fixture_texts(fixture)):
        labels.add("non-ascii text")
    if not fixture.recordings:
        labels.add("empty recordings")
    if any(recording.env_subset for recording in fixture.recordings):
        labels.add("env present")
    if any(rule.description == "" for rule in fixture.scrubbing_rules):
        labels.add("null description")
    return frozenset(labels)


def _assert_round_trips(fixture: FixtureFile) -> None:
    """Assert *fixture* survives the dict and file round trips unchanged."""
    rebuilt = FixtureFile.from_dict(fixture.to_dict())
    assert rebuilt == fixture, f"dict round trip changed the fixture: {rebuilt!r}"
    with tempfile.TemporaryDirectory() as tmp_dir:
        path = Path(tmp_dir) / "round-trip.json"
        fixture.save(path)
        loaded = FixtureFile.load(path)
    assert loaded == fixture, f"file round trip changed the fixture: {loaded!r}"


@given(candidate=fixture_files())
def test_fixture_file_round_trips(candidate: FixtureFile) -> None:
    """Every fixture the writer emits survives both round trips."""
    _assert_round_trips(candidate)


def test_legacy_golden_fixture_still_loads() -> None:
    """A legacy two-component fixture normalizes to the current schema."""
    loaded = FixtureFile.load(GOLDEN_FIXTURE_PATH)

    assert loaded.version == FixtureFile.SCHEMA_VERSION
    assert loaded.metadata.test_module is None
    assert loaded.recordings[0].stdin == "input\n"
    assert loaded.recordings[0].env_subset == {
        "GIT_AUTHOR_NAME": "Test User",
        "GIT_DIR": ".git",
    }
    assert loaded.scrubbing_rules[0].description == ""


@pytest.mark.parametrize("label", _ROUND_TRIP_LABELS)
def test_every_round_trip_label_is_reachable(label: str) -> None:
    """The fixture strategy produces each LOAD-1 coverage class."""
    found = hypothesis.find(
        fixture_files(),
        lambda candidate: label in classify_fixture(candidate),
        settings=settings(database=None, deadline=None),
    )

    assert label in classify_fixture(found)


def test_round_trip_check_rejects_a_reader_that_drops_empty_strings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The round-trip check can fail: an empty-hostile reader trips it."""
    witness = FixtureFile(
        version=FixtureFile.SCHEMA_VERSION,
        metadata=FixtureMetadata(
            created_at="2026-01-15T10:30:00+00:00",
            cmdmox_version="0.2.0",
            platform="linux",
            python_version="3.12.0",
        ),
        recordings=[
            RecordedInvocation(
                sequence=0,
                command="git",
                args=["status"],
                stdin="",
                env_subset={},
                stdout="ok\n",
                stderr="",
                exit_code=0,
                timestamp="2026-01-15T10:30:00+00:00",
                duration_ms=0,
            )
        ],
        scrubbing_rules=[
            ScrubbingRule(pattern="p", replacement="r", applied_to=[], description="")
        ],
    )
    original = fixture_module._require_str

    def reject_empty_strings(
        data: cabc.Mapping[str, object],
        key: str,
        *,
        path: str = "",
    ) -> str:
        """Reject an empty string as if the reader refused it."""
        value = original(data, key, path=path)
        if value == "":
            msg = f"Invalid fixture schema: {path}: unexpected empty string"
            raise ValueError(msg)
        return value

    monkeypatch.setattr(fixture_module, "_require_str", reject_empty_strings)

    with pytest.raises(ValueError, match="Invalid fixture schema"):
        _assert_round_trips(witness)
