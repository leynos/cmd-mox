#!/usr/bin/env python3
"""Filesystem and workflow adapters for pure release reconciliation decisions.

The workflow invokes this module as ``python3 -m scripts.release_assets_cli``.
All network access remains in its quoted ``curl`` and ``gh`` steps. This
adapter explicitly loads inventories, file digests, and DSSE subject claims;
it does not replace consumers' cryptographic Sigstore verification.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses as dc
import hashlib
import json
import os
import re
import shutil
import sys
import typing as typ
import urllib.parse
from pathlib import Path

from scripts import release_assets as assets

if typ.TYPE_CHECKING:
    import collections.abc as cabc

INDEX_URL = "https://pypi.org/simple/cmd-mox/"
_SHA256 = re.compile(r"[0-9a-f]{64}")


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


class _Arguments(argparse.Namespace):
    """Typed paths supplied to the workflow command adapter."""

    command: str
    publish: Path
    assets: Path
    index: Path | None
    github_files: Path | None
    bundles: Path | None
    output: Path | None


@dc.dataclass(frozen=True, slots=True)
class _Invocation:
    """One loaded command invocation with an explicit output destination."""

    state: assets.State
    args: _Arguments
    output_path: Path | None


def _digest(value: object) -> str:
    """Validate one expected SHA-256 digest.

    Returns
    -------
    str
        The validated lower-case hexadecimal digest.

    Raises
    ------
    ReleaseAssetError
        If the input is missing or violates its declared contract.
    """
    digest = value.lower() if isinstance(value, str) else ""
    if not _SHA256.fullmatch(digest):
        msg = f"invalid SHA-256 digest: {value}"
        raise assets.ReleaseAssetError(msg)
    return digest


def read_pypi_index(path: Path) -> dict[str, assets.PypiFile]:
    """Load PEP 691 filenames, SHA-256 digests, and resolved download URLs.

    File reads may raise ``OSError``; malformed JSON, fields, names, or
    digests raise ``ValueError``, ``KeyError``, ``TypeError``, or
    ``ReleaseAssetError``. The command boundary handles those failures.

    Parameters
    ----------
    path : pathlib.Path
        Saved PyPI Simple index JSON document.

    Returns
    -------
    dict[str, scripts.release_assets.PypiFile]
        Validated files keyed by safe filename; absent ``files`` means empty.

    """
    document = typ.cast("_Index", json.loads(path.read_text(encoding="utf-8")))
    result = {
        entry["filename"]: assets.PypiFile(
            _digest(entry["hashes"]["sha256"]),
            urllib.parse.urljoin(INDEX_URL, entry["url"]),
        )
        for entry in document.get("files", [])
    }
    assets.safe_names(result)
    return result


def read_github_assets(path: Path) -> dict[str, str | None]:
    """Load safe GitHub asset names and their optional SHA-256 digests.

    File reads may raise ``OSError``; malformed JSON, fields, names, or
    digests raise ``ValueError``, ``KeyError``, ``TypeError``, or
    ``ReleaseAssetError``. The command boundary handles those failures.

    Parameters
    ----------
    path : pathlib.Path
        Saved ``gh release view --json assets`` response.

    Returns
    -------
    dict[str, str or None]
        Asset inventory with unsupported digest types represented as absent.

    """
    document = typ.cast("_GitHubAssets", json.loads(path.read_text(encoding="utf-8")))
    result = {
        entry["name"]: _github_digest(entry.get("digest"))
        for entry in document.get("assets", [])
    }
    assets.safe_names(result)
    return result


def _github_digest(value: object) -> str | None:
    """Decode a supported GitHub digest without inventing absent hashes.

    Returns
    -------
    str or None
        The supported digest, or ``None`` for another digest form.
    """
    return (
        _digest(value.removeprefix("sha256:"))
        if isinstance(value, str) and value.startswith("sha256:")
        else None
    )


def sha256_of(path: Path) -> str:
    """Read one file and return its SHA-256 digest.

    Opening or reading the file may raise ``OSError``.

    Returns
    -------
    str
        Lower-case hexadecimal digest.

    """
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def local_names(directory: Path) -> list[str]:
    """Read a directory and validate its stable filename inventory.

    Directory reads may raise ``OSError``; unsafe entries raise
    ``UnsafeAssetNamesError``.

    Returns
    -------
    list[str]
        Sorted safe filenames.

    """
    return assets.safe_names(path.name for path in directory.iterdir())


def check_digest(path: Path, expected: str | None) -> None:
    """Read a file and reject bytes differing from an expected digest.

    Opening or reading the file may raise ``OSError``.

    Raises
    ------
    DigestMismatchError
        If the expected digest is absent or differs from the actual bytes.
    """
    actual = sha256_of(path)
    if actual != expected:
        raise assets.DigestMismatchError(path.name, actual, expected)


def read_bundle_subjects(path: Path) -> assets.Subjects:
    """Load exact filename/digest claims from a Sigstore DSSE bundle.

    File reads may raise ``OSError``; malformed JSON, base64, fields, or
    digests raise ``ValueError``, ``KeyError``, ``TypeError``, or
    ``ReleaseAssetError`` at this adapter boundary.

    Parameters
    ----------
    path : pathlib.Path
        Bundle emitted by ``actions/attest-build-provenance`` or retained by
        an earlier run. This reads claims; signature verification remains a
        separate consumer operation.

    Returns
    -------
    frozenset[tuple[str, str]]
        Safe subject names paired with validated SHA-256 digests.

    Raises
    ------
    ReleaseAssetError
        If the payload is not an in-toto SLSA build-provenance statement.
    """
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
    subjects = frozenset(
        (entry["name"], _digest(entry["digest"]["sha256"]))
        for entry in statement["subject"]
    )
    assets.safe_names(name for name, _ in subjects)
    return subjects


def write_output(path: Path, name: str, *, value: bool) -> None:
    """Append one workflow boolean to an explicitly supplied output path.

    Opening or writing the output file may raise ``OSError``.

    """
    with path.open("a", encoding="utf-8") as output:
        output.write(f"{name}={str(value).lower()}\n")


def _path(args: _Arguments, option: str) -> Path:
    """Require a command-specific input path at the adapter boundary.

    Returns
    -------
    pathlib.Path
        The explicitly supplied command input.

    Raises
    ------
    ReleaseAssetError
        If the input is missing or violates its declared contract.
    """
    paths = {
        "index": args.index,
        "github_files": args.github_files,
        "bundles": args.bundles,
        "output": args.output,
    }
    value = paths[option]
    if value is None:
        msg = f"--{option.replace('_', '-')} is required for {args.command}"
        raise assets.ReleaseAssetError(msg)
    return value


def _current_bundles(directory: Path) -> dict[str, assets.Subjects]:
    """Read only run-specific bundle files from the current attestation handoff.

    Returns
    -------
    dict[str, assets.Subjects]
        Current bundle names and their validated subject claims.

    Raises
    ------
    ReleaseAssetError
        If the input is missing or violates its declared contract.
    """
    result: dict[str, assets.Subjects] = {}
    for name in local_names(directory):
        if assets.bundle_family(name) is None:
            msg = f"unexpected provenance bundle name: {name}"
            raise assets.ReleaseAssetError(msg)
        result[name] = read_bundle_subjects(directory / name)
    return result


def _retained_bundles(
    state: assets.State, directory: Path, current_directory: Path
) -> dict[str, assets.Subjects]:
    """Read retained claims and check available GitHub asset digests.

    Returns
    -------
    dict[str, assets.Subjects]
        Inspected subject claims for bundles attached to this tag.
    """
    result: dict[str, assets.Subjects] = {}
    for name in assets.retained_bundle_names(state):
        path = directory / name
        if not path.is_file() and name in state.current_bundles:
            path = current_directory / name
            check_digest(path, state.github[name])
        elif state.github[name] is not None:
            check_digest(path, state.github[name])
        result[name] = read_bundle_subjects(path)
    return result


def _load_state(args: _Arguments) -> assets.State:
    """Load environmental inventories once before pure reconciliation.

    Returns
    -------
    assets.State
        An immutable snapshot with every required environmental input loaded.
    """
    index = _path(args, "index") if args.command in _INDEX_COMMANDS else None
    state = assets.State(
        {name: sha256_of(args.publish / name) for name in local_names(args.publish)},
        read_pypi_index(index) if index is not None else {},
        read_github_assets(args.assets),
    )
    needs_provenance = args.command in _PROVENANCE_COMMANDS and not (
        args.command == "stage-pypi" and not state.plan().pypi_uploads
    )
    if not needs_provenance:
        return state
    current_dir = _path(args, "bundles")
    state = dc.replace(state, current_bundles=_current_bundles(current_dir))
    if args.command in _RETAINED_COMMANDS:
        retained = _retained_bundles(state, _path(args, "github_files"), current_dir)
        return dc.replace(state, retained_bundles=retained)
    return state


def _carryover(invocation: _Invocation) -> None:
    """Print distribution and retained-bundle download names."""
    for name in assets.carryover(invocation.state):
        sys.stdout.write(f"{name}\n")


def _stage_pypi(invocation: _Invocation) -> None:
    """Execute the canonical PyPI copy/delete plan after provenance validation."""
    state, args = invocation.state, invocation.args
    decided = state.plan()
    if decided.pypi_uploads:
        assets.require_provenance(state)
    for name, source in sorted(decided.canonical.items()):
        artefact = args.publish / name
        if source is assets.Source.PYPI:
            sys.stdout.write(f"::notice::Skipping {name}: already on PyPI\n")
            artefact.unlink()
        elif source is assets.Source.GITHUB:
            carried = _path(args, "github_files") / name
            check_digest(carried, state.github[name])
            sys.stdout.write(f"::notice::Using the GitHub Release's bytes for {name}\n")
            shutil.copyfile(carried, artefact)
    if not decided.pypi_uploads:
        sys.stdout.write("Every artefact is already on PyPI; nothing to publish.\n")
    write_output(
        _output_path(invocation), "remaining", value=bool(decided.pypi_uploads)
    )


def _output_path(invocation: _Invocation) -> Path:
    """Require the output path that main explicitly loaded from the environment.

    Returns
    -------
    pathlib.Path
        The command output destination.

    Raises
    ------
    ReleaseAssetError
        If the input is missing or violates its declared contract.
    """
    if invocation.output_path is None:
        msg = "GITHUB_OUTPUT is required for this command"
        raise assets.ReleaseAssetError(msg)
    return invocation.output_path


def _pypi_fetches(state: assets.State, names: tuple[str, ...]) -> list[tuple[str, str]]:
    """Return selected PyPI downloads after enforcing HTTPS transport.

    Parameters
    ----------
    state : scripts.release_assets.State
        The immutable release inventory.
    names : tuple[str, ...]
        Selected filenames whose indexed URLs will be fetched.

    Returns
    -------
    list[tuple[str, str]]
        Filename and HTTPS URL pairs for the selected downloads.

    Raises
    ------
    scripts.release_assets.InsecurePypiDownloadError
        If any selected URL does not use HTTPS.
    """
    fetches = [(name, state.pypi[name].url) for name in names]
    if any(not url.startswith("https://") for _, url in fetches):
        raise assets.InsecurePypiDownloadError
    return fetches


def _copy_github_assets(
    args: _Arguments,
    decision: assets.GitHubStage,
    output: Path,
) -> None:
    """Copy selected local distributions and current-run bundles for upload."""
    for name in decision.local:
        shutil.copyfile(args.publish / name, output / name)
    bundle_directory = _path(args, "bundles")
    for name in decision.bundles:
        shutil.copyfile(bundle_directory / name, output / name)


def _stage_github(invocation: _Invocation) -> None:
    """Execute copy decisions and print validated PyPI fetch rows."""
    state, args = invocation.state, invocation.args
    decided = assets.stage_github(state)
    fetches = _pypi_fetches(state, decided.fetch)
    output = _path(args, "output")
    output.mkdir(parents=True, exist_ok=True)
    _copy_github_assets(args, decided, output)
    for name, url in fetches:
        sys.stdout.write(f"{name}\t{url}\n")
    write_output(
        _output_path(invocation),
        "pending",
        value=bool(decided.fetch or decided.local or decided.bundles),
    )


def _check_fetched(invocation: _Invocation) -> None:
    """Read each downloaded PyPI file and require its indexed digest."""
    state, args = invocation.state, invocation.args
    for name in sorted(state.plan().github_uploads & set(state.pypi)):
        check_digest(_path(args, "output") / name, state.pypi[name].sha256)


def _missing_pypi(invocation: _Invocation) -> None:
    """Print names still absent from a fresh PyPI index."""
    for name in assets.missing_from_pypi(invocation.state):
        sys.stdout.write(f"{name}\n")


def _verify(invocation: _Invocation) -> None:
    """Require matching destination digests and attached subject coverage.

    Raises
    ------
    ReleaseAssetError
        If destinations or attached provenance do not cover canonical bytes.
    """
    problems = assets.verify(invocation.state)
    if problems:
        raise assets.ReleaseAssetError("; ".join(problems))
    sys.stdout.write(
        "PyPI and the GitHub Release hold the same bytes for every artefact.\n"
    )


_COMMANDS: dict[str, cabc.Callable[[_Invocation], None]] = {
    "carryover": _carryover,
    "stage-pypi": _stage_pypi,
    "stage-github": _stage_github,
    "check-fetched": _check_fetched,
    "missing-pypi": _missing_pypi,
    "verify": _verify,
}
_INDEX_COMMANDS = frozenset({
    "stage-pypi",
    "stage-github",
    "check-fetched",
    "missing-pypi",
    "verify",
})
_PROVENANCE_COMMANDS = frozenset({"carryover", "stage-pypi", "stage-github", "verify"})
_RETAINED_COMMANDS = frozenset({"stage-pypi", "stage-github", "verify"})


def _parser() -> argparse.ArgumentParser:
    """Describe the workflow's explicit adapter inputs.

    Returns
    -------
    argparse.ArgumentParser
        The parser for the workflow command and its input paths.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=_COMMANDS)
    parser.add_argument("--publish", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    for option in ("index", "github-files", "bundles", "output"):
        parser.add_argument(f"--{option}", type=Path)
    return parser


def main(argv: cabc.Sequence[str] | None = None) -> int:
    """Load environmental inputs, execute a command, and annotate failures.

    Parameters
    ----------
    argv : collections.abc.Sequence[str] or None, optional
        Arguments after the program name; defaults to ``sys.argv[1:]``.

    Returns
    -------
    int
        Zero on success, one for handled input, filesystem, or policy errors.
    """
    args = _parser().parse_args(argv, namespace=_Arguments())
    try:
        output = (
            Path(os.environ["GITHUB_OUTPUT"])
            if args.command in {"stage-pypi", "stage-github"}
            else None
        )
        invocation = _Invocation(_load_state(args), args, output)
        _COMMANDS[args.command](invocation)
    except (
        assets.ReleaseAssetError,
        OSError,
        KeyError,
        ValueError,
        TypeError,
    ) as error:
        sys.stderr.write(f"::error title=release-assets::{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
