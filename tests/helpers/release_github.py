"""Fixture builders for GitHub release workflow step tests."""

from __future__ import annotations

import hashlib
import json
import typing as typ

from helpers.release_workflow import (
    install_fake_tools,
    load_workflow_step,
    provenance_bundle,
    run_bash_step,
)

if typ.TYPE_CHECKING:
    import subprocess
    from pathlib import Path


TAG = "v0.3.0-beta1"
SDIST = "cmd_mox-0.3.0.tar.gz"
WHEEL = "cmd_mox-0.3.0-py3-none-any.whl"


class PublicationInputs(typ.NamedTuple):
    """Describe the release files and destination state for one test."""

    github_assets: dict[str, object]
    bundle_name: str
    sdist: bytes = b"canonical sdist"
    wheel: bytes = b"canonical wheel"
    pypi_assets: dict[str, bytes] | None = None
    retained_subjects: dict[str, dict[str, bytes]] | None = None


def _write_json(path: Path, document: object) -> None:
    """Write a JSON document with stable UTF-8 encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _write_verified_subject_manifest(
    root: Path,
    inputs: PublicationInputs,
    built_assets: dict[str, bytes],
) -> None:
    """Model successful workflow signature checks for a later verify step."""
    index_assets = built_assets if inputs.pypi_assets is None else inputs.pypi_assets
    listed_assets = typ.cast(
        "list[dict[str, str]]", inputs.github_assets.get("assets", [])
    )
    rows = [
        f"{inputs.bundle_name}\t{name}\t{digest(content)}"
        for name, content in sorted(built_assets.items())
    ]
    for asset in listed_assets:
        name = asset["name"]
        if name.endswith(".sigstore.json"):
            subjects = (
                index_assets
                if inputs.retained_subjects is None
                else inputs.retained_subjects[name]
            )
            rows.extend(
                f"{name}\t{subject}\t{digest(content)}"
                for subject, content in sorted(subjects.items())
            )
    (root / "verified-subjects.tsv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )


def digest(content: bytes) -> str:
    """Return a lowercase SHA-256 digest for test asset content.

    Returns
    -------
    str
        SHA-256 hexadecimal digest.
    """
    return hashlib.sha256(content).hexdigest()


def gh_call(argv: list[str]) -> dict[str, object]:
    """Return one expected recording-stub call for the GitHub CLI.

    Returns
    -------
    dict[str, object]
        Tool name and exact argument vector in the fake-tool log format.
    """
    return {"tool": "gh", "argv": argv}


def build_github_assets(
    *, sdist: bytes, wheel: bytes, bundle: str | None = None
) -> dict[str, object]:
    """Return a GitHub API asset response for a release's distributions.

    Returns
    -------
    dict[str, object]
        GitHub assets with digests and an optional provenance bundle.
    """
    items: list[dict[str, str]] = [
        {"name": SDIST, "digest": f"sha256:{digest(sdist)}"},
        {"name": WHEEL, "digest": f"sha256:{digest(wheel)}"},
    ]
    if bundle is not None:
        bundle_digest = digest(provenance_bundle({SDIST: sdist, WHEEL: wheel}))
        items.append({"name": bundle, "digest": f"sha256:{bundle_digest}"})
    return {"assets": items}


def run_github_step(
    tmp_path: Path, step_id: str, extra_env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run one GitHub publication step with shared recording-tool defaults.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The captured process output and exit status.
    """
    tools = install_fake_tools(tmp_path / "bin")
    script, _ = load_workflow_step("publish-release", step_id)
    assets_path = tmp_path / "release-assets.json"
    assets_json = (
        assets_path.read_text(encoding="utf-8")
        if assets_path.is_file()
        else '{"assets": []}'
    )
    return run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(tmp_path / "tool-calls.jsonl"),
            "PATH": f"{tools}:/usr/bin:/bin",
            "GH_ASSETS_JSON": assets_json,
            "TAG_NAME": TAG,
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
            "PYPI_INDEX_PATH": "pypi-index.json",
            **extra_env,
        },
    )


def _write_matching_distribution_snapshot(
    retained: Path,
    asset: dict[str, str],
    index_assets: dict[str, bytes],
) -> None:
    """Write a retained distribution only when its GitHub digest matches."""
    name = asset["name"]
    content = index_assets.get(name)
    if content is None or asset.get("digest") != f"sha256:{digest(content)}":
        return
    (retained / name).write_bytes(content)


def write_publication_inputs(
    root: Path,
    inputs: PublicationInputs,
) -> tuple[Path, Path]:
    """Create local distributions, index and provenance bundle for a release.

    Returns
    -------
    tuple[pathlib.Path, pathlib.Path]
        The distribution directory and current-run bundle directory.
    """
    publish = root / "dist" / "publish"
    bundles = root / "dist" / "attestations"
    publish.mkdir(parents=True)
    bundles.mkdir()
    (publish / SDIST).write_bytes(inputs.sdist)
    (publish / WHEEL).write_bytes(inputs.wheel)
    built_assets = {SDIST: inputs.sdist, WHEEL: inputs.wheel}
    (bundles / inputs.bundle_name).write_bytes(provenance_bundle(built_assets))
    index_assets = built_assets if inputs.pypi_assets is None else inputs.pypi_assets
    index = {
        "files": [
            {
                "filename": name,
                "hashes": {"sha256": digest(content)},
                "url": f"https://files.example.invalid/{name}",
            }
            for name, content in index_assets.items()
        ]
    }
    retained = root / "github-state" / "files"
    retained.mkdir(parents=True)
    listed_assets = typ.cast(
        "list[dict[str, str]]", inputs.github_assets.get("assets", [])
    )
    for asset in listed_assets:
        name = asset["name"]
        if name.endswith(".sigstore.json"):
            subjects = (
                index_assets
                if inputs.retained_subjects is None
                else inputs.retained_subjects[name]
            )
            content = provenance_bundle(subjects)
            (retained / name).write_bytes(content)
            asset["digest"] = f"sha256:{digest(content)}"
        else:
            _write_matching_distribution_snapshot(retained, asset, index_assets)
    _write_json(root / "pypi-index.json", index)
    _write_json(root / "release-assets.json", inputs.github_assets)
    _write_json(root / "github-state" / "assets.json", inputs.github_assets)
    _write_verified_subject_manifest(root, inputs, built_assets)
    return publish, bundles


def prepare_snapshot_files(root: Path, *, missing_bundle: bool) -> tuple[Path, str]:
    """Seed local, remote and cached bytes for the draft snapshot step test.

    Parameters
    ----------
    root : pathlib.Path
        Scratch directory used by the shell-step test.
    missing_bundle : bool
        Whether the retained bundle should be absent from the local cache.

    Returns
    -------
    tuple[pathlib.Path, str]
        Download source directory and retained bundle filename.
    """
    publish = root / "dist" / "publish"
    publish.mkdir(parents=True)
    (publish / SDIST).write_bytes(b"new sdist")
    (publish / WHEEL).write_bytes(b"new wheel")

    downloads = root / "downloads"
    downloads.mkdir()
    (downloads / WHEEL).write_bytes(b"old wheel")
    prior_bundle = f"cmd-mox-{TAG}-run1-1.sigstore.json"
    (downloads / prior_bundle).write_bytes(provenance_bundle({WHEEL: b"old wheel"}))
    bundles = root / "dist" / "attestations"
    bundles.mkdir()
    (bundles / f"cmd-mox-{TAG}-run2-1.sigstore.json").write_bytes(
        provenance_bundle({SDIST: b"new sdist", WHEEL: b"new wheel"})
    )

    cached_files = root / "github-state" / "files"
    cached_files.mkdir(parents=True)
    (cached_files / WHEEL).write_bytes(b"old wheel")
    if not missing_bundle:
        (cached_files / prior_bundle).write_bytes(
            (downloads / prior_bundle).read_bytes()
        )
    return downloads, prior_bundle
