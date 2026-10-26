"""Pure release asset reconciliation decision tests."""

from __future__ import annotations

import typing as typ
from types import MappingProxyType

import hypothesis.strategies as st
import pytest
from hypothesis import given

assets = pytest.importorskip("scripts.release_assets")

_SAFE_NAMES = st.sets(
    st.integers(min_value=0, max_value=12).map(lambda n: f"asset-{n}.whl"), max_size=8
)


class _Provenance(typ.NamedTuple):
    """Bundle current and retained subject inventories for state fixtures."""

    current: dict[str, assets.Subjects] | None = None
    retained: dict[str, assets.Subjects] | None = None


def _state(
    *,
    local: dict[str, str],
    pypi: dict[str, str] | None = None,
    github: dict[str, str | None] | None = None,
    provenance: _Provenance | None = None,
) -> assets.State:
    """Build immutable inventories without filesystem or environment access.

    Returns
    -------
    assets.State
        The accepted pure inventory snapshot.
    """
    return assets.State(
        local,
        {
            name: assets.PypiFile(digest, f"https://pypi.test/{name}")
            for name, digest in (pypi or {}).items()
        },
        github or {},
        (provenance.current or {}) if provenance is not None else {},
        (provenance.retained or {}) if provenance is not None else {},
    )


@given(local=_SAFE_NAMES, pypi=_SAFE_NAMES, github=_SAFE_NAMES)
def test_plan_preserves_priority_partitioning_and_order_independence(
    local: set[str], pypi: set[str], github: set[str]
) -> None:
    """One relation holds for overlapping, reordered, and duplicate inventories."""
    decision = assets.plan(sorted(local), pypi, github)
    repeated = assets.plan([*sorted(local, reverse=True), *sorted(local)], pypi, github)
    assert decision == repeated, (
        "ordering and duplicate local inputs must not affect a plan"
    )
    assert decision.pypi_uploads == local - pypi, (
        "PyPI receives exactly its missing names"
    )
    assert decision.github_uploads == local - github, (
        "GitHub receives exactly its missing names"
    )
    assert set(decision.canonical) == local, (
        "every local name needs one canonical source"
    )
    for name, source in decision.canonical.items():
        expected = (
            assets.Source.PYPI
            if name in pypi
            else assets.Source.GITHUB
            if name in github
            else assets.Source.LOCAL
        )
        assert source is expected, (
            "canonical source priority must remain PyPI, GitHub, local"
        )
    rerun = assets.plan(
        local, pypi | decision.pypi_uploads, github | decision.github_uploads
    )
    assert not rerun.pypi_uploads, "a completed PyPI upload has no work on rerun"
    assert not rerun.github_uploads, "a completed GitHub upload has no work on rerun"


def test_state_copies_inventory_values_before_pure_queries() -> None:
    """A caller cannot change the accepted snapshot after constructing it."""
    local = {"a.whl": "local"}
    state = _state(local=local)
    local["b.whl"] = "changed"
    assert set(state.plan().canonical) == {"a.whl"}, (
        "the state must retain its immutable snapshot"
    )
    assert isinstance(state.local, MappingProxyType), (
        "the accepted inventory must expose a read-only mapping"
    )


def test_mismatches_describe_missing_and_different_destination_assets() -> None:
    """Each missing or differing destination gets a stable diagnostic."""
    assert assets.mismatches(
        ["absent.whl", "digest.whl", "github.whl", "same.whl"],
        {"digest.whl": "one", "github.whl": "two", "same.whl": "same"},
        {"digest.whl": "different", "github.whl": None, "same.whl": "same"},
    ) == [
        "absent.whl is missing from PyPI",
        "digest.whl differs: PyPI sha256 one, GitHub sha256 different",
        "github.whl is missing from the GitHub Release or has no digest",
    ], "each missing or differing destination must be identified"


def test_provenance_coverage_uses_subject_digests_instead_of_bundle_names() -> None:
    """A same-family bundle cannot authorize a new or changed distribution."""
    old = "cmd-mox-v0.3.0-run1-1.sigstore.json"
    current = "cmd-mox-v0.3.0-run2-1.sigstore.json"
    state = _state(
        local={"a.whl": "rebuilt", "new.whl": "new"},
        pypi={"a.whl": "old", "new.whl": "new"},
        github={"a.whl": "old", old: "bundle"},
        provenance=_Provenance(
            current={current: frozenset({("a.whl", "rebuilt"), ("new.whl", "new")})},
            retained={old: frozenset({("a.whl", "old")})},
        ),
    )
    decision = assets.stage_github(state)
    assert decision.bundles == (current,), (
        "a new canonical subject needs its current bundle"
    )
    assert decision.fetch == ("new.whl",), (
        "only the missing canonical distribution is fetched"
    )
    assert assets.verify(state) == [
        "new.whl is missing from the GitHub Release or has no digest",
        "new.whl has no matching provenance subject for sha256 new",
    ], "an unattached current bundle must not pass final verification"
    complete = _state(
        local=dict(state.local),
        pypi={"a.whl": "old", "new.whl": "new"},
        github={"a.whl": "old", "new.whl": "new", old: "bundle", current: "new-bundle"},
        provenance=_Provenance(
            current=dict(state.current_bundles),
            retained={
                **state.retained_bundles,
                current: state.current_bundles[current],
            },
        ),
    )
    assert assets.verify(complete) == [], (
        "attached matching subjects cover every canonical byte sequence"
    )
    assert assets.stage_github(complete).bundles == (), (
        "reruns must not attach redundant bundles"
    )


def test_unattested_carryover_fails_before_any_staging_decision() -> None:
    """Retained names with unrelated subjects fail closed."""
    old = "cmd-mox-v0.3.0-run1-1.sigstore.json"
    state = _state(
        local={"a.whl": "rebuilt"},
        pypi={"a.whl": "canonical"},
        github={old: "bundle"},
        provenance=_Provenance(retained={old: frozenset({("a.whl", "wrong")})}),
    )
    with pytest.raises(
        assets.UnattestedReleaseAssetsError,
        match=r"a\.whl has no matching provenance subject",
    ):
        assets.stage_github(state)
    with pytest.raises(assets.UnattestedReleaseAssetsError):
        assets.require_provenance(state)


def test_canonical_sources_and_carryover_include_only_matching_tag_bundles() -> None:
    """Remote canonical digests and matching retained bundles are explicit values."""
    current = "cmd-mox-v0.3.0-run2-1.sigstore.json"
    prior = "cmd-mox-v0.3.0-run1-1.sigstore.json"
    state = _state(
        local={"pypi.whl": "local", "github.whl": "local", "local.whl": "local"},
        pypi={"pypi.whl": "pypi"},
        github={
            "github.whl": "github",
            prior: "bundle",
            "cmd-mox-v9.0.0-run1-1.sigstore.json": "other",
        },
        provenance=_Provenance(current={current: frozenset()}),
    )
    assert assets.canonical_digests(state) == {
        "pypi.whl": "pypi",
        "github.whl": "github",
        "local.whl": "local",
    }, "canonical digests follow source priority"
    assert assets.carryover(state) == [prior, "github.whl"], (
        "only this tag's retained bundles may be carried over"
    )
    assert assets.missing_from_pypi(state) == ["github.whl", "local.whl"], (
        "missing names use the preloaded local snapshot"
    )
    incomplete = _state(local={"a.whl": "local"}, github={"a.whl": None})
    with pytest.raises(assets.ReleaseAssetError, match="no SHA-256 digest"):
        assets.canonical_digests(incomplete)


def test_pure_staging_returns_file_identifiers_without_transport_decisions() -> None:
    """Pure staging returns copy and fetch decisions without opening paths."""
    bundle = "cmd-mox-v0.3.0-run1-1.sigstore.json"
    state = _state(
        local={"a.whl": "digest"},
        provenance=_Provenance(current={bundle: frozenset({("a.whl", "digest")})}),
    )
    decision = assets.stage_github(state)
    assert decision.local == ("a.whl",), "fresh local bytes must be staged"
    assert decision.bundles == (bundle,), "their contributing bundle must be staged"
    insecure = assets.State(
        state.local,
        {"a.whl": assets.PypiFile("digest", "http://pypi.test/a.whl")},
        {},
        state.current_bundles,
        {},
    )
    assert assets.stage_github(insecure).fetch == ("a.whl",), (
        "the pure decision returns identifiers for the transport adapter"
    )
