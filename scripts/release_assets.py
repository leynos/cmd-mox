"""Pure decisions for immutable PyPI and GitHub Release reconciliation.

All inputs are immutable inventory snapshots. The CLI adapter loads JSON,
file digests, and Sigstore subjects before calling these functions, then
executes the returned copy, download, upload, and verification decisions.
"""

from __future__ import annotations

import dataclasses as dc
import enum
import re
import typing as typ
from types import MappingProxyType

if typ.TYPE_CHECKING:
    import collections.abc as cabc

Subjects = frozenset[tuple[str, str]]
_BUNDLE_NAME = re.compile(r"(?P<family>cmd-mox-.+)-run[0-9]+-[0-9]+\.sigstore\.json")


class ReleaseAssetError(Exception):
    """A release asset is missing, unsafe, or holds unexpected bytes."""


class InvalidAttestationPurposeError(ReleaseAssetError):
    """The attestation candidate command has an unsupported purpose."""

    def __init__(self) -> None:
        """Explain the supported workflow destinations."""
        super().__init__("--purpose must be pypi or github")


class DigestMismatchError(ReleaseAssetError):
    """A local or downloaded file does not match its expected SHA-256."""

    def __init__(self, name: str, actual: str, expected: str | None) -> None:
        """Include the actual and expected digest in the error detail."""
        super().__init__(f"{name} has sha256 {actual}, expected {expected}")


class UnattestedReleaseAssetsError(ReleaseAssetError):
    """Canonical distributions lack matching provenance subjects."""

    def __init__(self, problems: list[str]) -> None:
        """Identify every canonical filename and digest lacking provenance."""
        super().__init__("; ".join(problems))


class Source(enum.StrEnum):
    """Where one artefact name's canonical bytes come from."""

    PYPI = "pypi"
    GITHUB = "github"
    LOCAL = "local"


@dc.dataclass(frozen=True, slots=True)
class Plan:
    """The canonical source of each name and what each destination lacks."""

    canonical: cabc.Mapping[str, Source]
    pypi_uploads: frozenset[str]
    github_uploads: frozenset[str]


@dc.dataclass(frozen=True, slots=True)
class State:
    """Preloaded distribution, destination, and provenance inventories.

    ``local`` maps this build's filenames to SHA-256 digests. Bundle
    inventories map their immutable filenames to the exact subject/digest
    pairs decoded by the adapter. Retained bundles are downloaded from the
    release; current bundles may be attached when they add matching coverage.
    """

    local: cabc.Mapping[str, str]
    pypi: cabc.Mapping[str, str]
    github: cabc.Mapping[str, str | None]
    current_bundles: cabc.Mapping[str, Subjects] = dc.field(default_factory=dict)
    retained_bundles: cabc.Mapping[str, Subjects] = dc.field(default_factory=dict)

    def __post_init__(self) -> None:
        """Copy caller-owned mappings so every query sees one accepted snapshot."""
        object.__setattr__(self, "local", MappingProxyType(dict(self.local)))
        object.__setattr__(self, "pypi", MappingProxyType(dict(self.pypi)))
        object.__setattr__(self, "github", MappingProxyType(dict(self.github)))
        object.__setattr__(
            self,
            "current_bundles",
            MappingProxyType({
                name: frozenset(subjects)
                for name, subjects in self.current_bundles.items()
            }),
        )
        object.__setattr__(
            self,
            "retained_bundles",
            MappingProxyType({
                name: frozenset(subjects)
                for name, subjects in self.retained_bundles.items()
            }),
        )

    def plan(self) -> Plan:
        """Choose canonical sources and uploads without environmental reads.

        Returns
        -------
        Plan
            The deterministic plan for this immutable snapshot.
        """
        return plan(self.local, self.pypi, self.github)


@dc.dataclass(frozen=True, slots=True)
class GitHubStage:
    """Explicit files to fetch or copy before a GitHub upload."""

    fetch: tuple[str, ...]
    local: tuple[str, ...]
    bundles: tuple[str, ...]


def _canonical_source(
    name: str,
    on_pypi: cabc.Collection[str],
    on_github: cabc.Collection[str],
) -> Source:
    """Select the first existing source in release priority order.

    Returns
    -------
    Source
        The preferred existing source, or the local build when neither
        destination has the filename.
    """
    match (name in on_pypi, name in on_github):
        case (True, _):
            return Source.PYPI
        case (False, True):
            return Source.GITHUB
        case (False, False):
            return Source.LOCAL


def plan(
    local: cabc.Iterable[str],
    on_pypi: cabc.Collection[str],
    on_github: cabc.Collection[str],
) -> Plan:
    """Choose canonical sources in PyPI, GitHub, then local priority order.

    Parameters
    ----------
    local : collections.abc.Iterable[str]
        Names built and attested by this run.
    on_pypi : collections.abc.Collection[str]
        Names already held by PyPI.
    on_github : collections.abc.Collection[str]
        Names already held by the tag's GitHub Release.

    Returns
    -------
    Plan
        The canonical source and missing upload sets for every local name.

    Examples
    --------
    >>> plan(["cmd.whl"], set(), {"cmd.whl"}).canonical["cmd.whl"]
    <Source.GITHUB: 'github'>
    """
    names = frozenset(local)
    canonical = {
        name: _canonical_source(name, on_pypi, on_github) for name in sorted(names)
    }
    return Plan(
        MappingProxyType(canonical), names - set(on_pypi), names - set(on_github)
    )


def mismatches(
    local: cabc.Iterable[str],
    pypi_digests: cabc.Mapping[str, str],
    github_digests: cabc.Mapping[str, str | None],
) -> list[str]:
    """Describe distributions the two destinations do not hold identically.

    Parameters
    ----------
    local : collections.abc.Iterable[str]
        Filenames this release must carry.
    pypi_digests : collections.abc.Mapping[str, str]
        PyPI's indexed SHA-256 digests.
    github_digests : collections.abc.Mapping[str, str or None]
        GitHub's optional asset digests.

    Returns
    -------
    list[str]
        Stable missing-file or differing-digest diagnostics.
    """
    return [
        problem
        for name in sorted(set(local))
        if (problem := _mismatch(name, pypi_digests, github_digests))
    ]


def _mismatch(
    name: str, pypi: cabc.Mapping[str, str], github: cabc.Mapping[str, str | None]
) -> str | None:
    """Describe one destination disagreement, if present.

    Returns
    -------
    str or None
        A stable diagnostic, or ``None`` when both digests agree.
    """
    pypi_digest, github_digest = pypi.get(name), github.get(name)
    if pypi_digest is None:
        return f"{name} is missing from PyPI"
    if github_digest is None:
        return f"{name} is missing from the GitHub Release or has no digest"
    if pypi_digest != github_digest:
        return (
            f"{name} differs: PyPI sha256 {pypi_digest}, GitHub sha256 {github_digest}"
        )
    return None


def canonical_digests(state: State) -> dict[str, str]:
    """Resolve canonical digests from an accepted inventory snapshot.

    Parameters
    ----------
    state : State
        Local and destination inventories.

    Returns
    -------
    dict[str, str]
        The selected digest for every distribution filename.

    Raises
    ------
    ReleaseAssetError
        If a selected GitHub source has no SHA-256 digest.
    """
    result: dict[str, str] = {}
    for name, source in state.plan().canonical.items():
        digest = _source_digest(state, name, source)
        if digest is None:
            msg = f"{name} has no SHA-256 digest on its canonical source"
            raise ReleaseAssetError(msg)
        result[name] = digest
    return result


def _source_digest(state: State, name: str, source: Source) -> str | None:
    """Read the selected source digest from one accepted inventory snapshot.

    Returns
    -------
    str or None
        The selected SHA-256 digest, or ``None`` when GitHub omitted it.
    """
    match source:
        case Source.PYPI:
            return state.pypi[name]
        case Source.GITHUB:
            return state.github[name]
        case Source.LOCAL:
            return state.local[name]


def bundle_family(name: str) -> str | None:
    """Return the tag identity of a run-specific provenance bundle.

    Returns
    -------
    str or None
        Its tag-scoped family, or ``None`` for another asset name.
    """
    match = _BUNDLE_NAME.fullmatch(name)
    return match.group("family") if match is not None else None


def retained_bundle_names(state: State) -> list[str]:
    """Select existing bundle names belonging to this run's release tag.

    Returns
    -------
    list[str]
        Existing bundle names that must be downloaded and inspected.
    """
    families = {
        family for name in state.current_bundles if (family := bundle_family(name))
    }
    return sorted(name for name in state.github if bundle_family(name) in families)


def carryover(state: State) -> list[str]:
    """Select canonical GitHub files and retained bundles to download.

    Returns
    -------
    list[str]
        Distribution and tag-scoped bundle filenames required by later jobs.
    """
    return sorted(
        (set(state.local) & set(state.github)) | set(retained_bundle_names(state))
    )


def missing_from_pypi(state: State) -> list[str]:
    """List this run's distribution names not yet indexed by PyPI.

    Returns
    -------
    list[str]
        Stable missing-name inventory for propagation polling.
    """
    return sorted(set(state.local) - set(state.pypi))


def _attached_subjects(state: State) -> Subjects:
    """Collect subjects from inspected bundles attached to this release.

    Returns
    -------
    Subjects
        Exact claims retained by the current GitHub asset inventory.
    """
    return frozenset(
        subject
        for name, subjects in state.retained_bundles.items()
        if name in state.github
        for subject in subjects
    )


def _available_subjects(state: State) -> Subjects:
    """Add attachable current bundles to the retained subject inventory.

    Returns
    -------
    Subjects
        Claims already attached or available under a new immutable name.
    """
    return _attached_subjects(state) | frozenset(
        subject
        for name, subjects in state.current_bundles.items()
        if name not in state.github
        for subject in subjects
    )


def _provenance_problems(
    digests: cabc.Mapping[str, str], subjects: Subjects
) -> list[str]:
    """Describe canonical name/digest pairs absent from inspected subjects.

    Returns
    -------
    list[str]
        Stable diagnostics for every uncovered canonical distribution.
    """
    return [
        f"{name} has no matching provenance subject for sha256 {digest}"
        for name, digest in sorted(digests.items())
        if (name, digest) not in subjects
    ]


def require_provenance(state: State, names: cabc.Iterable[str] | None = None) -> None:
    """Reject canonical distributions without retained or attachable subjects.

    Parameters
    ----------
    state : State
        Preloaded distribution and provenance inventories.
    names : collections.abc.Iterable[str] or None, optional
        A subset whose bytes are about to be uploaded. ``None`` checks every
        canonical distribution.

    Raises
    ------
    UnattestedReleaseAssetsError
        If any canonical distribution lacks an exact name/digest subject.
    """
    digests = canonical_digests(state)
    selected = digests if names is None else {name: digests[name] for name in names}
    problems = _provenance_problems(selected, _available_subjects(state))
    if problems:
        raise UnattestedReleaseAssetsError(problems)


def stage_github(state: State) -> GitHubStage:
    """Plan GitHub's missing files and contributing provenance bundles.

    Canonical digest resolution and provenance validation can raise
    ``ReleaseAssetError`` or ``UnattestedReleaseAssetsError`` before a plan
    is returned; neither operation reads the environment.

    Parameters
    ----------
    state : State
        Preloaded local, destination, and decoded bundle inventories.

    Returns
    -------
    GitHubStage
        Distribution download identifiers and explicit local copy names.

    """
    require_provenance(state)
    lacking = sorted(state.plan().github_uploads)
    fetch = tuple(name for name in lacking if name in state.pypi)
    uncovered = set(canonical_digests(state).items()) - _attached_subjects(state)
    bundles: list[str] = []
    for name, subjects in sorted(state.current_bundles.items()):
        if name not in state.github and uncovered & subjects:
            bundles.append(name)
            uncovered -= subjects
    return GitHubStage(
        fetch, tuple(name for name in lacking if name not in state.pypi), tuple(bundles)
    )


def verify(state: State) -> list[str]:
    """Compare destination digests and require attached matching provenance.

    Canonical digest resolution raises ``ReleaseAssetError`` when a selected
    source has no recorded digest.

    Parameters
    ----------
    state : State
        Final destination inventories and inspected attached bundles.

    Returns
    -------
    list[str]
        Every missing file, digest disagreement, or unattested canonical byte.

    """
    pypi = dict(state.pypi)
    return [
        *mismatches(state.local, pypi, state.github),
        *_provenance_problems(canonical_digests(state), _attached_subjects(state)),
    ]
