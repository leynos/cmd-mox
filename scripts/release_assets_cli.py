#!/usr/bin/env python3
"""Command-line workflow adapter for release reconciliation decisions.

The workflow invokes this module as ``python3 -m scripts.release_assets_cli``.
It coordinates filesystem adapters with the workflow's GitHub CLI signature
checks before exposing release decisions.
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import os
import shutil
import sys
import typing as typ
from pathlib import Path

from scripts import release_assets as assets
from scripts import release_assets_io as asset_io

if typ.TYPE_CHECKING:
    import collections.abc as cabc


class _Arguments(argparse.Namespace):
    """Typed paths supplied to the workflow command adapter."""

    command: str
    publish: Path
    assets: Path
    index: Path | None
    github_files: Path | None
    bundles: Path | None
    output: Path | None
    verified_subjects: Path | None
    purpose: str | None


@dc.dataclass(frozen=True, slots=True)
class _Invocation:
    """One loaded command invocation with an explicit output destination."""

    state: assets.State
    args: _Arguments
    output_path: Path | None
    pypi_urls: dict[str, str]


@dc.dataclass(frozen=True, slots=True)
class _BundleInventory:
    """Current and retained bundle claims with their verified file paths."""

    state: assets.State
    current: dict[str, assets.Subjects]
    retained: dict[str, assets.Subjects]
    paths: dict[str, Path]
    combined: dict[str, assets.Subjects]


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
        "verified_subjects": args.verified_subjects,
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
    """
    result: dict[str, assets.Subjects] = {}
    for name in _validated_bundle_names(directory):
        result[name] = asset_io.read_bundle_subjects(directory / name)
    return result


def _validated_bundle_names(directory: Path) -> list[str]:
    """Return provenance bundle names after validating their tag family.

    Returns
    -------
    list[str]
        Valid bundle filenames in directory order.

    Raises
    ------
    ReleaseAssetError
        If a filename is not a release provenance bundle.
    """
    names = asset_io.local_names(directory)
    for name in names:
        if assets.bundle_family(name) is None:
            msg = f"unexpected provenance bundle name: {name}"
            raise assets.ReleaseAssetError(msg)
    return names


def _current_bundle_names(directory: Path) -> dict[str, assets.Subjects]:
    """Read current-run bundle names without interpreting their claims.

    Returns
    -------
    dict[str, assets.Subjects]
        Current bundle names mapped to empty subject inventories.
    """
    return {name: frozenset() for name in _validated_bundle_names(directory)}


def _retained_bundles(
    state: assets.State, directory: Path, current_directory: Path
) -> tuple[dict[str, assets.Subjects], dict[str, Path]]:
    """Read retained claims and check available GitHub asset digests.

    Returns
    -------
    tuple[dict[str, assets.Subjects], dict[str, pathlib.Path]]
        Inspected subject claims and their resolved input paths.
    """
    result: dict[str, assets.Subjects] = {}
    paths: dict[str, Path] = {}
    for name in assets.retained_bundle_names(state):
        path = directory / name
        if not path.is_file() and name in state.current_bundles:
            path = current_directory / name
            asset_io.check_digest(path, state.github[name])
        elif state.github[name] is not None:
            asset_io.check_digest(path, state.github[name])
        result[name] = asset_io.read_bundle_subjects(path)
        paths[name] = path
    return result, paths


def _verified_bundle_subjects(
    bundles: dict[str, assets.Subjects],
    verified: dict[str, assets.Subjects],
) -> dict[str, assets.Subjects]:
    """Keep only bundle subjects recorded by the workflow verifier.

    Returns
    -------
    dict[str, assets.Subjects]
        Subject claims grouped by bundle filename, limited to verified rows.
    """
    return {
        bundle: subjects & verified.get(bundle, frozenset())
        for bundle, subjects in bundles.items()
    }


def _load_state(args: _Arguments) -> tuple[assets.State, dict[str, str]]:
    """Load environmental inventories once before pure reconciliation.

    Returns
    -------
    tuple[assets.State, dict[str, str]]
        The immutable release snapshot and its adapter-owned PyPI URLs.
    """
    index = _load_index(args)
    state = _read_base_state(args, index)
    if args.command == "carryover":
        state = _with_current_bundle_names(args, state)
    if args.command in _OUTPUT_COMMANDS:
        _path(args, "output")
    if _needs_provenance(args, state):
        state = _with_verified_provenance(args, state)
    return state, index.urls


def _load_index(args: _Arguments) -> asset_io.PypiIndex:
    """Read the PyPI index only for commands that consume its inventory.

    Returns
    -------
    asset_io.PypiIndex
        Index digests and URLs, or empty maps for commands that do not need it.
    """
    if args.command not in _INDEX_COMMANDS:
        return asset_io.PypiIndex({}, {})
    return asset_io.read_pypi_index(_path(args, "index"))


def _read_base_state(args: _Arguments, index: asset_io.PypiIndex) -> assets.State:
    """Snapshot local build and destination inventories for one command.

    When GitHub omits an asset digest, downloaded bytes from the release
    snapshot or this run's upload staging directory provide the SHA-256. An
    asset with no downloaded bytes stays unresolved and fails closed if chosen.

    Returns
    -------
    assets.State
        The immutable release inventory snapshot.
    """
    local = {
        name: asset_io.sha256_of(args.publish / name)
        for name in asset_io.local_names(args.publish)
    }
    github = asset_io.read_github_assets(args.assets)
    download_directories = (args.github_files, args.output)
    for name, digest in github.items():
        if digest is not None:
            continue
        downloaded = next(
            (
                directory / name
                for directory in download_directories
                if directory is not None and (directory / name).is_file()
            ),
            None,
        )
        if downloaded is not None:
            github[name] = asset_io.sha256_of(downloaded)
    return assets.State(local, index.digests, github)


def _with_current_bundle_names(args: _Arguments, state: assets.State) -> assets.State:
    """Attach checked current-run bundle names for the carry-over listing.

    Returns
    -------
    assets.State
        The state with current-run bundle names attached.
    """
    names = _current_bundle_names(_path(args, "bundles"))
    return dc.replace(state, current_bundles=names)


def _needs_provenance(args: _Arguments, state: assets.State) -> bool:
    """Select commands needing trusted subjects for actual publication bytes.

    Returns
    -------
    bool
        Whether command execution needs verified bundle subjects.
    """
    if args.command not in _PROVENANCE_COMMANDS:
        return False
    return args.command != "stage-pypi" or bool(state.plan().pypi_uploads)


def _with_verified_provenance(args: _Arguments, state: assets.State) -> assets.State:
    """Load bundle claims and keep subjects verified against exact bytes.

    Returns
    -------
    assets.State
        The state with only cryptographically verified subjects retained.
    """
    inventory = _read_bundle_inventory(args, state)
    verified_rows = asset_io.read_verified_subjects(_path(args, "verified_subjects"))
    verified = _verified_bundle_subjects(inventory.combined, verified_rows)
    canonical = assets.canonical_digests(inventory.state)
    relevant_names = (
        inventory.state.plan().pypi_uploads
        if args.command == "stage-pypi"
        else canonical
    )
    required_subjects = frozenset((name, canonical[name]) for name in relevant_names)
    verified = {
        bundle: subjects & required_subjects for bundle, subjects in verified.items()
    }
    return dc.replace(
        inventory.state,
        current_bundles={name: verified[name] for name in inventory.current},
        retained_bundles={name: verified[name] for name in inventory.retained},
    )


def _read_bundle_inventory(args: _Arguments, state: assets.State) -> _BundleInventory:
    """Load current and retained bundle claims without trusting their subjects.

    Returns
    -------
    _BundleInventory
        Raw subject claims and their bundle paths for later verification.
    """
    current_dir = _path(args, "bundles")
    current = _current_bundles(current_dir)
    state_with_current = dc.replace(state, current_bundles=current)
    retained_dir = (
        _path(args, "github_files") if args.command in _RETAINED_COMMANDS else None
    )
    retained, retained_paths = (
        _retained_bundles(state_with_current, retained_dir, current_dir)
        if retained_dir is not None
        else ({}, {})
    )
    paths = {name: current_dir / name for name in current}
    paths.update(retained_paths)
    return _BundleInventory(
        state_with_current, current, retained, paths, {**current, **retained}
    )


def _artifact_directories(args: _Arguments) -> list[Path]:
    """Collect directories that may contain bytes matching bundle subjects.

    Returns
    -------
    list[pathlib.Path]
        Existing workflow input directories in which artifact bytes may occur.
    """
    result = [args.publish]
    if args.github_files is not None:
        result.append(args.github_files)
    if args.output is not None:
        result.append(args.output)
    return result


def _attestation_checks(invocation: _Invocation) -> None:
    """Print digest-matched artifact/bundle pairs for the workflow verifier.

    Raises
    ------
    InvalidAttestationPurposeError
        If the requested workflow destination is not supported.
    """
    args, state = invocation.args, invocation.state
    if args.purpose not in {"pypi", "github"}:
        raise assets.InvalidAttestationPurposeError
    if args.purpose == "github":
        _path(args, "output")
    inventory = _read_bundle_inventory(args, state)
    canonical = assets.canonical_digests(inventory.state)
    names = state.plan().pypi_uploads if args.purpose == "pypi" else canonical.keys()
    required = frozenset((name, canonical[name]) for name in names)
    directories = _artifact_directories(args)
    for bundle, subjects in sorted(inventory.combined.items()):
        bundle_path = inventory.paths[bundle]
        for name, digest in sorted(subjects & required):
            artifact = asset_io.matching_artifact(name, digest, directories)
            if artifact is not None:
                sys.stdout.write(
                    f"{artifact}\t{bundle_path}\t{bundle}\t{name}\t{digest}\n"
                )


def _carryover(invocation: _Invocation) -> None:
    """Print distribution and retained-bundle download names."""
    for name in assets.carryover(invocation.state):
        sys.stdout.write(f"{name}\n")


def _stage_pypi_asset(
    invocation: _Invocation, name: str, source: assets.Source
) -> None:
    """Apply one PyPI staging decision without replacing an existing upload."""
    state, args = invocation.state, invocation.args
    match source:
        case assets.Source.PYPI:
            sys.stdout.write(f"::notice::Skipping {name}: already on PyPI\n")
            (args.publish / name).unlink()
        case assets.Source.GITHUB:
            carried = _path(args, "github_files") / name
            asset_io.check_digest(carried, state.github[name])
            sys.stdout.write(f"::notice::Using the GitHub Release's bytes for {name}\n")
            shutil.copyfile(carried, args.publish / name)
        case assets.Source.LOCAL:
            return


def _stage_pypi(invocation: _Invocation) -> None:
    """Execute the canonical PyPI copy/delete plan after provenance validation."""
    state = invocation.state
    decided = state.plan()
    if decided.pypi_uploads:
        assets.require_provenance(state, decided.pypi_uploads)
    for name, source in sorted(decided.canonical.items()):
        _stage_pypi_asset(invocation, name, source)
    if not decided.pypi_uploads:
        sys.stdout.write("Every artefact is already on PyPI; nothing to publish.\n")
    asset_io.write_output(
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
        msg = f"GITHUB_OUTPUT is required for {invocation.args.command}"
        raise assets.ReleaseAssetError(msg)
    return invocation.output_path


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
    """Execute verified copy decisions after the workflow fetched PyPI bytes."""
    state, args = invocation.state, invocation.args
    decided = assets.stage_github(state)
    output = _path(args, "output")
    output.mkdir(parents=True, exist_ok=True)
    _copy_github_assets(args, decided, output)
    asset_io.write_output(
        _output_path(invocation),
        "pending",
        value=bool(decided.fetch or decided.local or decided.bundles),
    )


def _fetch_github(invocation: _Invocation) -> None:
    """Print PyPI downloads needed before provenance validation for GitHub."""
    names = tuple(
        sorted(invocation.state.plan().github_uploads & set(invocation.state.pypi))
    )
    for name, url in asset_io.pypi_fetches(invocation.pypi_urls, names):
        sys.stdout.write(f"{name}\t{url}\n")


def _check_fetched(invocation: _Invocation) -> None:
    """Read each downloaded PyPI file and require its indexed digest."""
    state, args = invocation.state, invocation.args
    for name in sorted(state.plan().github_uploads & set(state.pypi)):
        asset_io.check_digest(_path(args, "output") / name, state.pypi[name])


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
    "attestation-checks": _attestation_checks,
    "stage-pypi": _stage_pypi,
    "fetch-github": _fetch_github,
    "stage-github": _stage_github,
    "check-fetched": _check_fetched,
    "missing-pypi": _missing_pypi,
    "verify": _verify,
}
_INDEX_COMMANDS = frozenset({
    "attestation-checks",
    "stage-pypi",
    "fetch-github",
    "stage-github",
    "check-fetched",
    "missing-pypi",
    "verify",
})
_PROVENANCE_COMMANDS = frozenset({"stage-pypi", "stage-github", "verify"})
_RETAINED_COMMANDS = frozenset({
    "attestation-checks",
    "stage-pypi",
    "stage-github",
    "verify",
})
_OUTPUT_COMMANDS = frozenset({"stage-github", "verify"})


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
    parser.add_argument("--verified-subjects", type=Path)
    parser.add_argument("--purpose", choices=("pypi", "github"))
    return parser


def _workflow_output_path(command: str) -> Path | None:
    """Resolve the workflow output path required by output-producing commands.

    Returns
    -------
    pathlib.Path or None
        The output path for a publishing command, or ``None`` otherwise.

    Raises
    ------
    ReleaseAssetError
        If a command that emits workflow outputs lacks ``GITHUB_OUTPUT``.
    """
    if command not in {"stage-pypi", "stage-github"}:
        return None
    raw_output = os.environ.get("GITHUB_OUTPUT")
    if not raw_output:
        msg = f"GITHUB_OUTPUT is required for {command}"
        raise assets.ReleaseAssetError(msg)
    return Path(raw_output)


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
        output = _workflow_output_path(args.command)
        state, pypi_urls = _load_state(args)
        invocation = _Invocation(state, args, output, pypi_urls)
        _COMMANDS[args.command](invocation)
    except (assets.ReleaseAssetError, OSError, ValueError) as error:
        sys.stderr.write(f"::error title=release-assets::{error}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
