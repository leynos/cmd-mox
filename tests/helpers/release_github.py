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
    return run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(tmp_path / "tool-calls.jsonl"),
            "PATH": f"{tools}:/usr/bin:/bin",
            "TAG_NAME": TAG,
            **extra_env,
        },
    )


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
    _write_json(root / "pypi-index.json", index)
    _write_json(root / "release-assets.json", inputs.github_assets)
    _write_json(root / "github-state" / "assets.json", inputs.github_assets)
    _write_verified_subject_manifest(root, inputs, built_assets)
    return publish, bundles
