"""Reconcile release artefacts so PyPI and GitHub hold the same bytes.

Each filename's canonical bytes are the ones already published: PyPI first,
then an existing GitHub Release asset, and this run's attested local file only
when neither destination has the name. Each destination receives only names
it lacks; remote assets are never overwritten. The CLI keeps network access in
workflow ``curl`` and ``gh`` steps and uses only the standard library.
"""

from __future__ import annotations

import dataclasses as dc
import enum
import hashlib
import json
import os
import re
import shutil
import sys
import typing as typ
import urllib.parse
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

INDEX_URL = "https://pypi.org/simple/cmd-mox/"
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_DIGEST_PREFIX = "sha256:"
_BUNDLE_NAME = re.compile(r"(?P<family>cmd-mox-.+)-run[0-9]+-[0-9]+\.sigstore\.json")


class ReleaseAssetError(Exception):
    """A release asset is missing, unsafe, or holds unexpected bytes."""

    def __init__(self, message: str) -> None:
        """Store the actionable detail emitted by the workflow annotation."""
        super().__init__(message)


class UnsafeAssetNamesError(ReleaseAssetError):
    """One or more filenames cannot safely be passed to GitHub CLI."""

    def __init__(self, names: list[str]) -> None:
        """Include unsafe names in the workflow error detail."""
        super().__init__(f"refusing unsafe artefact names: {names}")


class DigestMismatchError(ReleaseAssetError):
    """A local or downloaded file does not match its expected SHA-256."""

    def __init__(self, name: str, actual: str, expected: str | None) -> None:
        """Include the actual and expected digest in the error detail."""
        super().__init__(f"{name} has sha256 {actual}, expected {expected}")


class InsecurePypiDownloadError(ReleaseAssetError):
    """A selected PyPI download URL does not use HTTPS."""

    def __init__(self) -> None:
        """Explain why an insecure download cannot be used."""
        super().__init__("every PyPI download must use HTTPS")


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


def plan(
    local: cabc.Iterable[str],
    on_pypi: cabc.Collection[str],
    on_github: cabc.Collection[str],
) -> Plan:
    """Choose canonical bytes and the names each destination still needs.

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
        PyPI takes precedence over GitHub, and GitHub over local build bytes.

    Examples
    --------
    >>> plan(["cmd.whl"], set(), {"cmd.whl"}).canonical["cmd.whl"]
    <Source.GITHUB: 'github'>
    """
    names = frozenset(local)
    canonical = {
        name: Source.PYPI
        if name in on_pypi
        else Source.GITHUB
        if name in on_github
        else Source.LOCAL
        for name in names
    }
    return Plan(
        canonical=canonical,
        pypi_uploads=frozenset(name for name in names if name not in on_pypi),
        github_uploads=frozenset(name for name in names if name not in on_github),
    )


def mismatches(
    local: cabc.Iterable[str],
    pypi_digests: cabc.Mapping[str, str],
    github_digests: cabc.Mapping[str, str | None],
) -> list[str]:
    """Describe names the two destinations do not hold identically.

    Parameters
    ----------
    local : collections.abc.Iterable[str]
        Names the release must carry.
    pypi_digests : collections.abc.Mapping[str, str]
        PyPI's SHA-256 digest for each name it holds.
    github_digests : collections.abc.Mapping[str, str | None]
        GitHub's optional SHA-256 digest for each release asset.

    Returns
    -------
    list[str]
        One message per missing or differing name; empty when they agree.

    Examples
    --------
    >>> mismatches(["cmd.whl"], {"cmd.whl": "ab"}, {"cmd.whl": "ab"})
    []
    """
    return [
        problem
        for name in sorted(set(local))
        if (problem := _mismatch(name, pypi_digests, github_digests))
    ]


def _mismatch(
    name: str,
    pypi_digests: cabc.Mapping[str, str],
    github_digests: cabc.Mapping[str, str | None],
) -> str | None:
    """Describe how one name differs between the destinations, if it does.

    Returns
    -------
    str or None
        A mismatch explanation, or ``None`` when the digests agree.
    """
    pypi, github = pypi_digests.get(name), github_digests.get(name)
    if pypi is None:
        return f"{name} is missing from PyPI"
    if github is None:
        return f"{name} is missing from the GitHub Release or has no digest"
    if pypi != github:
        return f"{name} differs: PyPI sha256 {pypi}, GitHub sha256 {github}"
    return None


@dc.dataclass(frozen=True, slots=True)
class PypiFile:
    """One file listed by the PyPI simple index."""

    sha256: str
    url: str


def read_pypi_index(path: Path) -> dict[str, PypiFile]:
    """Map PEP 691 filenames to their SHA-256 digest and resolved URL.

    Returns
    -------
    dict[str, PypiFile]
        Each index filename with its digest and absolute download URL.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        entry["filename"]: PypiFile(
            sha256=entry["hashes"]["sha256"].lower(),
            url=urllib.parse.urljoin(INDEX_URL, entry["url"]),
        )
        for entry in document.get("files", [])
    }


def read_github_assets(path: Path) -> dict[str, str | None]:
    """Map GitHub Release asset names to their optional SHA-256 digest.

    Returns
    -------
    dict[str, str or None]
        Asset names and their parsed SHA-256 digest, when available.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    return {
        asset["name"]: _digest_hex(asset.get("digest"))
        for asset in document.get("assets", [])
    }


def _digest_hex(digest: object) -> str | None:
    """Return the hex of a ``sha256:`` digest or ``None`` for another form.

    Returns
    -------
    str or None
        Lower-case digest hex, or ``None`` when the value is unsupported.
    """
    if isinstance(digest, str) and digest.startswith(_DIGEST_PREFIX):
        return digest.removeprefix(_DIGEST_PREFIX).lower()
    return None


def sha256_of(path: Path) -> str:
    """Return the SHA-256 hex digest of one file's bytes.

    Returns
    -------
    str
        Lower-case SHA-256 hex digest.
    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def local_names(directory: Path) -> list[str]:
    """List safe filenames in ``directory`` in stable order.

    Returns
    -------
    list[str]
        Sorted filenames accepted by the release-asset filename rule.

    Raises
    ------
    UnsafeAssetNamesError
        If any filename is unsafe to pass to the GitHub CLI.
    """
    names = sorted(path.name for path in directory.iterdir())
    unsafe = [name for name in names if not _SAFE_NAME.fullmatch(name)]
    if unsafe:
        raise UnsafeAssetNamesError(unsafe)
    return names


def _check_digest(path: Path, expected: str | None) -> None:
    """Raise unless ``path`` holds bytes with the expected digest.

    Raises
    ------
    DigestMismatchError
        If the expected digest is absent or does not match the file.
    """
    actual = sha256_of(path)
    if expected is None or actual != expected:
        raise DigestMismatchError(path.name, actual, expected)


def write_output(name: str, *, value: bool) -> None:
    """Append one lower-case boolean output to ``GITHUB_OUTPUT``."""
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"{name}={str(value).lower()}\n")


@dc.dataclass(frozen=True, slots=True)
class State:
    """The run's artefacts and what each destination already holds."""

    publish: Path
    pypi: dict[str, PypiFile]
    github: dict[str, str | None]

    @classmethod
    def load(cls, publish: Path, index: Path | None, assets: Path) -> State:
        """Read the staged artefacts, PyPI index, and GitHub asset listing.

        Returns
        -------
        State
            The parsed index state and the paths used by reconciliation.
        """
        pypi = read_pypi_index(index) if index is not None else {}
        return cls(publish, pypi, read_github_assets(assets))

    def plan(self) -> Plan:
        """Plan this run's uploads against both destinations.

        Returns
        -------
        Plan
            Canonical sources and the remaining uploads for each destination.
        """
        return plan(local_names(self.publish), self.pypi, self.github)


def carryover(state: State) -> list[str]:
    """Return asset names whose GitHub bytes PyPI may need.

    Returns
    -------
    list[str]
        Names present in this run and on the existing GitHub Release.
    """
    return sorted(set(local_names(state.publish)) & set(state.github))


def stage_pypi(state: State, github_files: Path) -> bool:
    """Leave exactly the canonical files PyPI still needs in ``publish``.

    Parameters
    ----------
    state : State
        The staged files and both destinations' asset listings.
    github_files : pathlib.Path
        Downloaded GitHub Release assets from the draft-release job.

    Returns
    -------
    bool
        Whether any artefact remains to upload.
    """
    decided = state.plan()
    for name, source in sorted(decided.canonical.items()):
        artefact = state.publish / name
        if source is Source.PYPI:
            sys.stdout.write(f"::notice::Skipping {name}: already on PyPI\n")
            artefact.unlink()
        elif source is Source.GITHUB:
            carried = github_files / name
            _check_digest(carried, state.github[name])
            sys.stdout.write(f"::notice::Using the GitHub Release's bytes for {name}\n")
            shutil.copyfile(carried, artefact)
    if not decided.pypi_uploads:
        sys.stdout.write("Every artefact is already on PyPI; nothing to publish.\n")
    return bool(decided.pypi_uploads)


def _pypi_fetch_list(
    lacking: cabc.Iterable[str], pypi: cabc.Mapping[str, PypiFile]
) -> list[tuple[str, str]]:
    """Return PyPI (name, URL) pairs to fetch, insisting on HTTPS.

    Returns
    -------
    list[tuple[str, str]]
        Names that GitHub lacks and their PyPI download URLs.

    Raises
    ------
    InsecurePypiDownloadError
        If any selected PyPI download URL does not use HTTPS.
    """
    fetch = [(name, pypi[name].url) for name in lacking if name in pypi]
    if any(not url.startswith("https://") for _, url in fetch):
        raise InsecurePypiDownloadError
    return fetch


def _copy_pypi_gap(
    lacking: cabc.Iterable[str],
    pypi: cabc.Mapping[str, PypiFile],
    publish: Path,
    output: Path,
) -> None:
    """Copy missing names absent from PyPI into ``output``."""
    for name in lacking:
        if name not in pypi:
            shutil.copyfile(publish / name, output / name)


def _bundle_family(name: str) -> str | None:
    """Return the tag-scoped identity of a run-specific provenance bundle.

    Returns
    -------
    str or None
        The tag-scoped bundle family, or ``None`` for an unrelated filename.
    """
    match = _BUNDLE_NAME.fullmatch(name)
    return match.group("family") if match is not None else None


def _has_bundle_for_release(name: str, github: cabc.Mapping[str, str | None]) -> bool:
    """Report whether GitHub already carries this name or its tag's bundle.

    Returns
    -------
    bool
        Whether an exact asset or a bundle for the same tag already exists.
    """
    family = _bundle_family(name)
    return name in github or (
        family is not None and any(_bundle_family(asset) == family for asset in github)
    )


def _copy_unattached_bundles(
    bundles: Path, github: cabc.Mapping[str, str | None], output: Path
) -> None:
    """Copy a tag's provenance bundle only when the release has none yet."""
    for name in local_names(bundles):
        if not _has_bundle_for_release(name, github):
            shutil.copyfile(bundles / name, output / name)


def stage_github(state: State, bundles: Path, output: Path) -> list[tuple[str, str]]:
    """Stage assets GitHub lacks and return PyPI files that need fetching.

    Parameters
    ----------
    state : State
        The staged artefacts and both destinations' asset listings.
    bundles : pathlib.Path
        This run's Sigstore provenance bundle directory.
    output : pathlib.Path
        Directory receiving assets to upload and PyPI download targets.

    Returns
    -------
    list[tuple[str, str]]
        PyPI filenames and HTTPS URLs whose bytes GitHub must receive.
    """
    lacking = sorted(state.plan().github_uploads)
    fetch = _pypi_fetch_list(lacking, state.pypi)
    output.mkdir(parents=True, exist_ok=True)
    _copy_pypi_gap(lacking, state.pypi, state.publish, output)
    _copy_unattached_bundles(bundles, state.github, output)
    return fetch


def check_fetched(state: State, output: Path) -> None:
    """Raise unless fetched PyPI files match their indexed SHA-256 digests."""
    for name in sorted(state.plan().github_uploads & set(state.pypi)):
        _check_digest(output / name, state.pypi[name].sha256)


def verify(state: State, bundles: Path) -> list[str]:
    """Describe every artefact or tag bundle absent from the GitHub Release.

    Parameters
    ----------
    state : State
        The staged artefacts and both destinations' asset listings.
    bundles : pathlib.Path
        This run's Sigstore provenance bundle directory.

    Returns
    -------
    list[str]
        Missing or mismatched artefacts and missing tag-scoped provenance.
    """
    pypi = {name: file.sha256 for name, file in state.pypi.items()}
    problems = mismatches(local_names(state.publish), pypi, state.github)
    missing = [
        name
        for name in local_names(bundles)
        if not _has_bundle_for_release(name, state.github)
    ]
    return [
        *problems,
        *(f"{name} is missing from the GitHub Release" for name in missing),
    ]
