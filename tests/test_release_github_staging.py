"""Tests for successful GitHub asset staging from canonical sources."""

from __future__ import annotations

import json
import typing as typ

from helpers.release_github import (
    SDIST,
    TAG,
    WHEEL,
    PublicationInputs,
    build_github_assets,
    gh_call,
    run_github_step,
    write_publication_inputs,
)
from helpers.release_workflow import read_tool_calls
from helpers.release_workflow import (
    release_shell_marks as pytestmark,
)

__all__ = ["pytestmark"]

if typ.TYPE_CHECKING:
    from pathlib import Path


def test_stage_github_stages_local_assets_and_attestation_bundle(
    tmp_path: Path,
) -> None:
    """GitHub gets this run's assets and bundle when no destination has them."""
    current_bundle = f"cmd-mox-{TAG}-run41-1.sigstore.json"
    write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets={"assets": []},
            bundle_name=current_bundle,
            pypi_assets={},
        ),
    )
    result = run_github_step(
        tmp_path,
        "stage",
        {
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    output = tmp_path / "github-upload"
    assert result.returncode == 0, result.stderr
    assert {path.name for path in output.iterdir()} == {
        SDIST,
        WHEEL,
        current_bundle,
    }, "local canonical distributions must be staged with their bundle"
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == (
        "pending=true\n"
    ), "missing release assets must enable the upload step"
    upload_result = run_github_step(tmp_path, "upload", {})
    assert upload_result.returncode == 0, upload_result.stderr
    assert read_tool_calls(tmp_path / "tool-calls.jsonl", tool="gh")[-1] == gh_call([
        "release",
        "upload",
        TAG,
        *sorted([
            f"github-upload/{SDIST}",
            f"github-upload/{WHEEL}",
            f"github-upload/{current_bundle}",
        ]),
    ]), "the release upload must include only staged files and no clobber flag"


def test_pypi_sourced_rerun_keeps_existing_bundle_and_bytes(tmp_path: Path) -> None:
    """A rerun uses PyPI's bytes without attaching a bundle for rebuilt files."""
    prior_bundle = f"cmd-mox-{TAG}-run40-1.sigstore.json"
    current_bundle = f"cmd-mox-{TAG}-run41-1.sigstore.json"
    pypi_sdist = b"previously published sdist"
    pypi_wheel = b"previously published wheel"
    curl_downloads = tmp_path / "curl-downloads"
    curl_downloads.mkdir()
    (curl_downloads / SDIST).write_bytes(pypi_sdist)
    (curl_downloads / WHEEL).write_bytes(pypi_wheel)
    write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets={"assets": [{"name": prior_bundle}]},
            bundle_name=current_bundle,
            pypi_assets={SDIST: pypi_sdist, WHEEL: pypi_wheel},
        ),
    )

    result = run_github_step(
        tmp_path,
        "stage",
        {
            "CURL_DOWNLOADS": str(curl_downloads),
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    output = tmp_path / "github-upload"
    assert result.returncode == 0, result.stderr
    assert {path.name for path in output.iterdir()} == {SDIST, WHEEL}, (
        "PyPI carry-overs must be staged without this run's unrelated bundle"
    )
    assert (output / SDIST).read_bytes() == pypi_sdist, (
        "GitHub must receive PyPI's canonical sdist bytes"
    )
    assert (output / WHEEL).read_bytes() == pypi_wheel, (
        "GitHub must receive PyPI's canonical wheel bytes"
    )
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == (
        "pending=true\n"
    ), "staging missing GitHub assets must enable the upload step"

    assets = build_github_assets(
        sdist=pypi_sdist, wheel=pypi_wheel, bundle=prior_bundle
    )
    verify_result = run_github_step(
        tmp_path,
        "verify",
        {
            "GH_ASSETS_JSON": json.dumps(assets),
            "PYPI_INDEX_PATH": "pypi-index.json",
        },
    )
    assert verify_result.returncode == 0, verify_result.stderr
    assert "hold the same bytes" in verify_result.stdout, (
        "verification must confirm that both destinations agree"
    )


def test_first_pypi_publication_can_attach_this_runs_matching_bundle(
    tmp_path: Path,
) -> None:
    """The first run may attach its bundle after PyPI accepts its exact bytes."""
    bundle = f"cmd-mox-{TAG}-run41-1.sigstore.json"
    curl_downloads = tmp_path / "curl-downloads"
    curl_downloads.mkdir()
    (curl_downloads / SDIST).write_bytes(b"canonical sdist")
    (curl_downloads / WHEEL).write_bytes(b"canonical wheel")
    write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets={"assets": []},
            bundle_name=bundle,
            pypi_assets={
                SDIST: b"canonical sdist",
                WHEEL: b"canonical wheel",
            },
        ),
    )

    result = run_github_step(
        tmp_path,
        "stage",
        {
            "CURL_DOWNLOADS": str(curl_downloads),
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    output = tmp_path / "github-upload"
    assert result.returncode == 0, result.stderr
    assert {path.name for path in output.iterdir()} == {
        SDIST,
        WHEEL,
        bundle,
    }, "matching PyPI and attested bytes must carry the current run's bundle"
    assert (output / SDIST).read_bytes() == b"canonical sdist", (
        "the staged sdist must match the attested build"
    )
    assert (output / WHEEL).read_bytes() == b"canonical wheel", (
        "the staged wheel must match the attested build"
    )
