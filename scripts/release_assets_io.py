"""Filesystem, registry, and signature adapters for release reconciliation."""

from __future__ import annotations

import base64
import dataclasses as dc
import hashlib
import json
import re
import typing as typ
import urllib.parse

from scripts import release_assets as assets

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

INDEX_URL = "https://pypi.org/simple/cmd-mox/"
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")
_VERIFIED_SUBJECT_FIELD_COUNT = 3


class UnsafeAssetNamesError(assets.ReleaseAssetError):
    """One or more untrusted filenames cannot safely reach the GitHub CLI."""

    def __init__(self, names: list[str]) -> None:
        """Include unsafe names in the workflow error detail."""
        super().__init__(f"refusing unsafe artefact names: {names}")


class InsecurePypiDownloadError(assets.ReleaseAssetError):
    """A selected PyPI download URL does not use HTTPS."""

    def __init__(self) -> None:
        """Explain why an insecure download cannot be used."""
        super().__init__("every PyPI download must use HTTPS")


class _IndexFile(typ.TypedDict):
    """One PEP 691 file record."""

    filename: str
    hashes: dict[str, str]
    url: str


class _Index(typ.TypedDict, total=False):
    """A PEP 691 document with an optional file inventory."""

    files: list[_IndexFile]


class _GitHubAsset(typ.TypedDict):
    """One GitHub asset record."""

    name: str
    digest: typ.NotRequired[str | None]


class _GitHubAssets(typ.TypedDict, total=False):
    """A GitHub release asset response."""

    assets: list[_GitHubAsset]


class _Subject(typ.TypedDict):
    """One decoded in-toto subject."""

    name: str
    digest: dict[str, str]


class _Statement(typ.TypedDict):
    """The build-provenance statement fields this adapter consumes."""

    _type: str
    predicateType: str
    subject: list[_Subject]


class _Envelope(typ.TypedDict):
    """The DSSE content fields needed to inspect subject claims."""

    payloadType: str
    payload: str


class _Bundle(typ.TypedDict):
    """A Sigstore bundle containing a DSSE envelope."""

    dsseEnvelope: _Envelope


@dc.dataclass(frozen=True, slots=True)
class PypiIndex:
    """Validated PyPI digests and transport URLs."""

    digests: dict[str, str]
    urls: dict[str, str]


def _digest(value: object) -> str:
    """Validate one expected SHA-256 digest.

    Returns
    -------
    str
        The validated lower-case hexadecimal digest.

    Raises
    ------
    ReleaseAssetError
        If the value is not a SHA-256 digest.
    """
    match value:
        case str() as text:
            digest = text.lower()
        case _:
            digest = ""
    if not _SHA256.fullmatch(digest):
        msg = f"invalid SHA-256 digest: {value}"
        raise assets.ReleaseAssetError(msg)
    return digest


def read_pypi_index(path: Path) -> PypiIndex:
    """Load PEP 691 filenames, digests, and resolved download URLs.

    Returns
    -------
    PypiIndex
        Validated digests and URLs; an absent ``files`` field is empty.

    Raises
    ------
    ReleaseAssetError
        If the PEP 691 document has an invalid schema or unsafe filename.
    """
    try:
        document = typ.cast("_Index", json.loads(path.read_text(encoding="utf-8")))
        entries = document.get("files", [])
        digests = {
            entry["filename"]: _digest(entry["hashes"]["sha256"]) for entry in entries
        }
        urls = {
            entry["filename"]: urllib.parse.urljoin(INDEX_URL, entry["url"])
            for entry in entries
        }
        safe_names(digests)
    except (AttributeError, KeyError, TypeError) as error:
        msg = f"invalid PEP 691 PyPI index: {path.name}"
        raise assets.ReleaseAssetError(msg) from error
    return PypiIndex(digests, urls)


def read_github_assets(path: Path) -> dict[str, str | None]:
    """Load safe GitHub asset names and optional SHA-256 digests.

    Returns
    -------
    dict[str, str or None]
        Asset inventory with unsupported digest types represented as absent.

    Raises
    ------
    ReleaseAssetError
        If the release response has an invalid schema or unsafe filename.
    """
    try:
        document = typ.cast(
            "_GitHubAssets", json.loads(path.read_text(encoding="utf-8"))
        )
        result = {
            entry["name"]: _github_digest(entry.get("digest"))
            for entry in document.get("assets", [])
        }
        safe_names(result)
    except (AttributeError, KeyError, TypeError) as error:
        msg = f"invalid GitHub release asset inventory: {path.name}"
        raise assets.ReleaseAssetError(msg) from error
    return result


def _github_digest(value: object) -> str | None:
    """Decode a supported GitHub digest without inventing absent hashes.

    Returns
    -------
    str or None
        A validated digest, or ``None`` for another digest form.
    """
    match value:
        case str() as digest if digest.startswith("sha256:"):
            return _digest(digest.removeprefix("sha256:"))
        case _:
            return None


def sha256_of(path: Path) -> str:
    """Read one file and return its lower-case SHA-256 digest.

    Returns
    -------
    str
        The lower-case hexadecimal digest.
    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def safe_names(names: cabc.Iterable[str]) -> list[str]:
    """Validate untrusted asset names and return a stable inventory.

    Returns
    -------
    list[str]
        Sorted, unique filenames matching the accepted syntax.

    Raises
    ------
    UnsafeAssetNamesError
        If any filename falls outside the accepted asset-name syntax.
    """
    result = sorted(set(names))
    unsafe = [name for name in result if not _SAFE_NAME.fullmatch(name)]
    if unsafe:
        raise UnsafeAssetNamesError(unsafe)
    return result


def local_names(directory: Path) -> list[str]:
    """Read a directory and validate its stable filename inventory.

    Returns
    -------
    list[str]
        Sorted safe filenames.
    """
    return safe_names(path.name for path in directory.iterdir())


def check_digest(path: Path, expected: str | None) -> None:
    """Read a file and reject bytes that differ from an expected digest.

    Raises
    ------
    DigestMismatchError
        If the expected digest is absent or differs from the file contents.
    """
    actual = sha256_of(path)
    if actual != expected:
        raise assets.DigestMismatchError(path.name, actual, expected)


def read_bundle_subjects(path: Path) -> assets.Subjects:
    """Load exact filename/digest claims from a Sigstore DSSE bundle.

    The returned claims remain untrusted until separately checked against an
    artefact and the expected signer identity.

    Returns
    -------
    assets.Subjects
        Safe subject names paired with validated SHA-256 digests.

    Raises
    ------
    ReleaseAssetError
        If the payload is not an in-toto SLSA build-provenance statement.
    """
    statement = _provenance_statement(path)
    try:
        subjects = frozenset(
            (entry["name"], _digest(entry["digest"]["sha256"]))
            for entry in statement["subject"]
        )
        safe_names(name for name, _ in subjects)
    except (AttributeError, KeyError, TypeError) as error:
        msg = f"invalid Sigstore bundle structure: {path.name}"
        raise assets.ReleaseAssetError(msg) from error
    return subjects


def _provenance_statement(path: Path) -> _Statement:
    """Decode and validate the SLSA statement carried by one bundle.

    Returns
    -------
    _Statement
        A decoded SLSA build-provenance statement.

    Raises
    ------
    ReleaseAssetError
        If the bundle envelope is malformed or uses another statement type.
    """
    try:
        document = typ.cast("_Bundle", json.loads(path.read_text(encoding="utf-8")))
        envelope = document["dsseEnvelope"]
        if envelope["payloadType"] != "application/vnd.in-toto+json":
            msg = f"{path.name} is not an in-toto DSSE bundle"
            raise assets.ReleaseAssetError(msg)
        payload = base64.b64decode(envelope["payload"], validate=True)
        statement = typ.cast("_Statement", json.loads(payload))
        if (
            statement["_type"] != "https://in-toto.io/Statement/v1"
            or statement["predicateType"] != "https://slsa.dev/provenance/v1"
        ):
            msg = f"{path.name} is not SLSA build provenance"
            raise assets.ReleaseAssetError(msg)
    except (AttributeError, KeyError, TypeError) as error:
        msg = f"invalid Sigstore bundle structure: {path.name}"
        raise assets.ReleaseAssetError(msg) from error
    return statement


def write_output(path: Path, name: str, *, value: bool) -> None:
    """Append one workflow boolean to an explicitly supplied output path."""
    with path.open("a", encoding="utf-8") as output:
        output.write(f"{name}={str(value).lower()}\n")


def read_verified_subjects(path: Path) -> dict[str, assets.Subjects]:
    """Read subject rows verified by the workflow's GitHub CLI step.

    Returns
    -------
    dict[str, assets.Subjects]
        Verified subjects grouped by provenance bundle filename.

    Raises
    ------
    ReleaseAssetError
        If a row is malformed or contains an unsafe filename or digest.
    """
    result: dict[str, set[tuple[str, str]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split("\t")
        if len(fields) != _VERIFIED_SUBJECT_FIELD_COUNT:
            msg = "verified subject rows must contain bundle, filename, and digest"
            raise assets.ReleaseAssetError(msg)
        bundle, name, raw_digest = fields
        safe_names((bundle, name))
        result.setdefault(bundle, set()).add((name, _digest(raw_digest)))
    return {bundle: frozenset(subjects) for bundle, subjects in result.items()}


def matching_artifact(
    name: str, digest: str, directories: cabc.Iterable[Path]
) -> Path | None:
    """Find local bytes that match one untrusted bundle subject exactly.

    Returns
    -------
    pathlib.Path or None
        The first matching artifact path, if available.
    """
    for directory in directories:
        artifact = directory / name
        if artifact.is_file() and sha256_of(artifact) == digest:
            return artifact
    return None


def pypi_fetches(
    pypi_urls: cabc.Mapping[str, str], names: cabc.Iterable[str]
) -> list[tuple[str, str]]:
    """Return selected PyPI downloads after enforcing HTTPS transport.

    Returns
    -------
    list[tuple[str, str]]
        Filename and HTTPS URL pairs for selected downloads.

    Raises
    ------
    InsecurePypiDownloadError
        If any selected URL does not use HTTPS.
    """
    fetches = [(name, pypi_urls[name]) for name in names]
    if any(not url.startswith("https://") for _, url in fetches):
        raise InsecurePypiDownloadError
    return fetches
