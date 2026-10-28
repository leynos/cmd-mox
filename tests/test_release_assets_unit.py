"""Pure release asset reconciliation decision tests."""

from __future__ import annotations

import importlib
import typing as typ
from pathlib import Path
from types import MappingProxyType

import hypothesis.strategies as st
import pytest
from helpers.release_workflow import (
    RELEASE_WORKFLOW_PATH,
    should_skip_release_tests_for_mutation_sandbox,
)
from hypothesis import given

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_ASSETS_CLI = _REPOSITORY_ROOT / "scripts" / "release_assets_cli.py"
if should_skip_release_tests_for_mutation_sandbox(
    Path.cwd(), RELEASE_WORKFLOW_PATH, _RELEASE_ASSETS_CLI
):
    pytest.skip(
        "mutmut sandbox omits the release workflow and CLI", allow_module_level=True
    )

assets = typ.cast("typ.Any", importlib.import_module("scripts.release_assets"))

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_SAFE_NAMES = st.sets(
    st.integers(min_value=0, max_value=12).map(lambda n: f"asset-{n}.whl"), max_size=8
)


class _Provenance(typ.NamedTuple):
    """Bundle current and retained subject inventories for state fixtures."""

    current: dict[str, assets.Subjects] | None = None
    retained: dict[str, assets.Subjects] | None = None


class _SourceInventories(typ.NamedTuple):
    """Generated destination inventories and their original plan."""

    local: dict[str, str]
    pypi: dict[str, str]
    github: dict[str, str]
    decision: assets.Plan
    selected: dict[str, str]


class _DigestInventories(typ.NamedTuple):
    """Independent digest maps used to calculate expected source bytes."""

    local: dict[str, str]
    pypi: dict[str, str]
    github: dict[str, str]


class _ReconciliationExample(typ.NamedTuple):
    """One generated starting state and its modelled completed transition."""

    initial: assets.State
    completed: assets.State
    selected: dict[str, str]
    stage: assets.GitHubStage
    has_digest_conflict: bool


def _state(
    *,
    local: dict[str, str],
    pypi: dict[str, str] | None = None,
    github: cabc.Mapping[str, str | None] | None = None,
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
        pypi or {},
        github or {},
        (provenance.current or {}) if provenance is not None else {},
        (provenance.retained or {}) if provenance is not None else {},
    )


def _expected_digest(
    name: str,
    source: assets.Source,
    inventories: _DigestInventories,
) -> str:
    """Select an expected digest independently from the production resolver.

    Returns
    -------
    str
        The digest from the independently supplied source inventory.

    Raises
    ------
    AssertionError
        If the source enum gains an unsupported member.
    """
    match source:
        case assets.Source.PYPI:
            return inventories.pypi[name]
        case assets.Source.GITHUB:
            return inventories.github[name]
        case assets.Source.LOCAL:
            return inventories.local[name]
        case _:
            raise AssertionError


def _expected_source(
    name: str, pypi_names: set[str], github_names: set[str]
) -> assets.Source:
    """Select the expected source from independently supplied name sets.

    Returns
    -------
    assets.Source
        Canonical source under PyPI, GitHub, then local priority.
    """
    if name in pypi_names:
        return assets.Source.PYPI
    if name in github_names:
        return assets.Source.GITHUB
    return assets.Source.LOCAL


def _source_inventories(
    local_names: set[str],
    pypi_names: set[str],
    github_names: set[str],
    digest_seed: int,
) -> _SourceInventories:
    """Generate valid local and destination inventories for one model case.

    Returns
    -------
    _SourceInventories
        The inventories, selected plan, and canonical digests.
    """
    local = {
        name: f"{digest_seed + index:064x}"
        for index, name in enumerate(sorted(local_names))
    }
    pypi = {
        name: f"{digest_seed + 100 + index:064x}"
        for index, name in enumerate(sorted(local_names & pypi_names))
    }
    github = {
        name: f"{digest_seed + 200 + index:064x}"
        for index, name in enumerate(sorted(local_names & github_names))
    }
    decision = assets.plan(local, pypi, github)
    digest_inventories = _DigestInventories(local, pypi, github)
    selected = {
        name: _expected_digest(name, source, digest_inventories)
        for name, source in decision.canonical.items()
    }
    return _SourceInventories(local, pypi, github, decision, selected)


def _provenance_inventories(
    inventories: _SourceInventories, digest_seed: int
) -> tuple[dict[str, str], _Provenance]:
    """Create retained remote and current local subject inventories.

    Returns
    -------
    tuple[dict[str, str], _Provenance]
        The GitHub bundle asset and decoded current/retained subjects.
    """
    local_subjects = frozenset(
        (name, digest)
        for name, digest in inventories.selected.items()
        if inventories.decision.canonical[name] is assets.Source.LOCAL
    )
    retained_name = "cmd-mox-v1.2.3-run1-1.sigstore.json"
    current_name = "cmd-mox-v1.2.3-run2-1.sigstore.json"
    github = dict(inventories.github)
    if any(
        source is not assets.Source.LOCAL
        for source in inventories.decision.canonical.values()
    ):
        github[retained_name] = f"{digest_seed + 300:064x}"
        retained = {retained_name: frozenset(inventories.selected.items())}
        current = {current_name: local_subjects}
    else:
        retained = {}
        current = {current_name: frozenset(inventories.selected.items())}
    return github, _Provenance(current=current, retained=retained)


def _initial_reconciliation_state(
    inventories: _SourceInventories, digest_seed: int
) -> assets.State:
    """Build a state with subjects for its selected existing or local bytes.

    Returns
    -------
    assets.State
        The generated immutable starting inventory.
    """
    github, provenance = _provenance_inventories(inventories, digest_seed)
    return _state(
        local=inventories.local,
        pypi=inventories.pypi,
        github=github,
        provenance=provenance,
    )


def _complete_reconciliation(
    state: assets.State, inventories: _SourceInventories, digest_seed: int
) -> tuple[assets.State, assets.GitHubStage]:
    """Model uploads of missing assets and attachment of needed bundles.

    Returns
    -------
    tuple[assets.State, assets.GitHubStage]
        The completed inventory and the initial GitHub staging decision.
    """
    stage = assets.stage_github(state)
    pypi_after = dict(state.pypi)
    for name in inventories.decision.pypi_uploads:
        pypi_after[name] = inventories.selected[name]
    github_after = dict(state.github)
    for name in inventories.decision.github_uploads:
        github_after[name] = inventories.selected[name]
    retained_after = dict(state.retained_bundles)
    for name in stage.bundles:
        github_after[name] = f"{digest_seed + 400:064x}"
        retained_after[name] = state.current_bundles[name]
    return (
        assets.State(
            state.local,
            pypi_after,
            github_after,
            state.current_bundles,
            retained_after,
        ),
        stage,
    )


def _reconciliation_example(
    local_names: set[str],
    pypi_names: set[str],
    github_names: set[str],
    digest_seed: int,
) -> _ReconciliationExample:
    """Create an initial inventory and model one complete transition.

    Returns
    -------
    _ReconciliationExample
        The generated states, canonical digests, staging plan, and conflict bit.
    """
    inventories = _source_inventories(
        local_names, pypi_names, github_names, digest_seed
    )
    initial = _initial_reconciliation_state(inventories, digest_seed)
    completed, stage = _complete_reconciliation(initial, inventories, digest_seed)
    has_conflict = any(
        name in initial.github and digest != initial.github[name]
        for name, digest in initial.pypi.items()
    )
    return _ReconciliationExample(
        initial, completed, inventories.selected, stage, has_conflict
    )


def _assert_missing_provenance_is_rejected(
    example: _ReconciliationExample,
) -> None:
    """Prove every generated nonempty inventory fails without any bundle."""
    if not example.selected:
        return
    without_provenance = _state(
        local=dict(example.initial.local),
        pypi=dict(example.initial.pypi),
        github=dict(example.initial.github),
    )
    with pytest.raises(assets.UnattestedReleaseAssetsError):
        assets.stage_github(without_provenance)


def _assert_preexisting_assets_are_unchanged(example: _ReconciliationExample) -> None:
    """Assert the modelled transition leaves every initial filename intact."""
    for name, digest in example.initial.pypi.items():
        assert example.completed.pypi[name] == digest, (
            "existing PyPI files must never change"
        )
    for name, digest in example.initial.github.items():
        assert example.completed.github[name] == digest, (
            "existing GitHub files must never change"
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
        expected = _expected_source(name, pypi, github)
        assert source is expected, (
            "canonical source priority must remain PyPI, GitHub, local"
        )
    rerun = assets.plan(
        local, pypi | decision.pypi_uploads, github | decision.github_uploads
    )
    assert not rerun.pypi_uploads, "a completed PyPI upload has no work on rerun"
    assert not rerun.github_uploads, "a completed GitHub upload has no work on rerun"


@given(
    local_names=_SAFE_NAMES,
    pypi_names=_SAFE_NAMES,
    github_names=_SAFE_NAMES,
    digest_seed=st.integers(min_value=1, max_value=2**32),
)
def test_reconciliation_reruns_preserve_canonical_bytes_without_overwrites(
    local_names: set[str],
    pypi_names: set[str],
    github_names: set[str],
    digest_seed: int,
) -> None:
    """A completed reconciliation is a no-op and preserves every chosen digest."""
    example = _reconciliation_example(
        local_names, pypi_names, github_names, digest_seed
    )
    reordered = assets.State(
        dict(reversed(tuple(example.initial.local.items()))),
        dict(reversed(tuple(example.initial.pypi.items()))),
        dict(reversed(tuple(example.initial.github.items()))),
        dict(reversed(tuple(example.initial.current_bundles.items()))),
        dict(reversed(tuple(example.initial.retained_bundles.items()))),
    )
    assert assets.stage_github(reordered) == example.stage, (
        "inventory insertion order must not change staging decisions"
    )
    assert assets.canonical_digests(reordered) == example.selected, (
        "inventory insertion order must not change canonical bytes"
    )
    _assert_missing_provenance_is_rejected(example)
    assert bool(assets.verify(example.completed)) is example.has_digest_conflict, (
        "pre-existing conflicting assets must fail verification safely"
    )
    _assert_preexisting_assets_are_unchanged(example)
    assert assets.canonical_digests(example.completed) == example.selected, (
        "staging must preserve the digest selected from the original inventories"
    )
    assert not example.completed.plan().pypi_uploads, (
        "a second PyPI plan must not request an overwrite"
    )
    assert not example.completed.plan().github_uploads, (
        "a second GitHub plan must not request an overwrite"
    )
    assert assets.stage_github(example.completed).bundles == (), (
        "a second GitHub staging pass must not attach provenance again"
    )


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


def test_provenance_coverage_rejects_subjects_split_across_bundles() -> None:
    """Separate builds cannot jointly attest one canonical distribution set."""
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
    with pytest.raises(
        assets.UnattestedReleaseAssetsError,
        match="no single provenance bundle covers the complete distribution set",
    ):
        assets.stage_github(state)


def test_one_provenance_bundle_must_cover_the_complete_distribution_set() -> None:
    """Distinct builds cannot jointly attest one canonical sdist and wheel pair."""
    first = "cmd-mox-v0.3.0-run1-1.sigstore.json"
    second = "cmd-mox-v0.3.0-run2-1.sigstore.json"
    split = _state(
        local={"cmd_mox-0.3.0.tar.gz": "sdist-a", "cmd_mox-0.3.0.whl": "wheel-a"},
        github={second: "bundle"},
        provenance=_Provenance(
            current={first: frozenset({("cmd_mox-0.3.0.tar.gz", "sdist-a")})},
            retained={second: frozenset({("cmd_mox-0.3.0.whl", "wheel-a")})},
        ),
    )

    with pytest.raises(
        assets.UnattestedReleaseAssetsError,
        match="no single provenance bundle covers the complete distribution set",
    ):
        assets.require_provenance(split)

    complete = _state(
        local=dict(split.local),
        provenance=_Provenance(
            current={
                first: frozenset({
                    ("cmd_mox-0.3.0.tar.gz", "sdist-a"),
                    ("cmd_mox-0.3.0.whl", "wheel-a"),
                })
            }
        ),
    )
    assets.require_provenance(complete)


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


def test_source_digest_rejects_an_unhandled_source_member() -> None:
    """An invalid source value fails at the exhaustive match boundary."""
    state = _state(local={"a.whl": "local"})

    with pytest.raises(AssertionError):
        assets._source_digest(state, "a.whl", typ.cast("assets.Source", "unknown"))


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
        {"a.whl": "digest"},
        {},
        state.current_bundles,
        {},
    )
    assert assets.stage_github(insecure).fetch == ("a.whl",), (
        "the pure decision returns identifiers for the transport adapter"
    )
