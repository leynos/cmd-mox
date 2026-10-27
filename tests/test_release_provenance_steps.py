"""Release workflow tests for provenance carry-over and subject coverage."""

from __future__ import annotations

import json
import typing as typ
from pathlib import Path

import pytest
from helpers.release_github import (
    SDIST,
    TAG,
    WHEEL,
    PublicationInputs,
    build_github_assets,
    digest,
    run_github_step,
    write_publication_inputs,
)
from helpers.release_workflow import (
    load_workflow_step,
    provenance_bundle,
    read_tool_calls,
    run_bash_step,
)
from helpers.release_workflow import (
    release_shell_marks as pytestmark,
)

__all__ = ["pytestmark"]


def test_failed_job_rerun_refreshes_attached_current_bundle_from_github(
    tmp_path: Path,
) -> None:
    """A bundle uploaded in a failed job is refreshed from current release state."""
    bundle = f"cmd-mox-{TAG}-run41-1.sigstore.json"
    _, current_bundles = write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets=build_github_assets(
                sdist=b"canonical sdist",
                wheel=b"canonical wheel",
                bundle=bundle,
            ),
            bundle_name=bundle,
        ),
    )
    snapshot_bundle = tmp_path / "github-state" / "files" / bundle
    snapshot_bundle.unlink()
    bundle_bytes = (current_bundles / bundle).read_bytes()
    downloads = tmp_path / "github-downloads"
    downloads.mkdir()
    (downloads / SDIST).write_bytes(b"canonical sdist")
    (downloads / WHEEL).write_bytes(b"canonical wheel")
    (downloads / bundle).write_bytes(bundle_bytes)

    result = run_github_step(
        tmp_path,
        "stage",
        {
            "GH_DOWNLOADS": str(downloads),
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    calls = read_tool_calls(tmp_path / "tool-calls.jsonl", tool="gh")
    attestation_calls = [
        typ.cast("list[str]", call["argv"])
        for call in calls
        if typ.cast("list[str]", call["argv"])[0:2] == ["attestation", "verify"]
    ]
    assert result.returncode == 0, result.stderr
    assert len(attestation_calls) == 2, "both distributions must be verified"
    bundle_paths = {
        (tmp_path / Path(argv[argv.index("--bundle") + 1])).resolve()
        for argv in attestation_calls
    }
    assert bundle_paths == {snapshot_bundle.resolve()}, (
        "verification must use the bundle downloaded from the fresh GitHub inventory"
    )
    assert snapshot_bundle.read_bytes() == bundle_bytes, (
        "the fresh release bundle must reach the later staging decisions"
    )


def test_new_filename_retains_old_subjects_and_adds_its_matching_current_bundle(
    tmp_path: Path,
) -> None:
    """A same-version addition needs both the retained and current provenance."""
    prior = f"cmd-mox-{TAG}-run40-1.sigstore.json"
    current = f"cmd-mox-{TAG}-run41-1.sigstore.json"
    extra = "cmd_mox-0.3.0-extra-py3-none-any.whl"
    old = {SDIST: b"canonical sdist", WHEEL: b"old wheel"}
    gh_assets = build_github_assets(sdist=old[SDIST], wheel=old[WHEEL], bundle=prior)
    publish, bundles = write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets=gh_assets,
            bundle_name=current,
            wheel=b"rebuilt wheel",
            pypi_assets={**old, extra: b"new wheel filename"},
            retained_subjects={prior: old},
        ),
    )
    (publish / extra).write_bytes(b"new wheel filename")
    (bundles / current).write_bytes(
        provenance_bundle({
            SDIST: b"canonical sdist",
            WHEEL: b"rebuilt wheel",
            extra: b"new wheel filename",
        })
    )
    for name, content in old.items():
        (tmp_path / "github-state" / "files" / name).write_bytes(content)
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / extra).write_bytes(b"new wheel filename")
    result = run_github_step(
        tmp_path,
        "stage",
        {
            "CURL_DOWNLOADS": str(tmp_path / "downloads"),
            "PYPI_INDEX_PATH": "pypi-index.json",
        },
    )
    assert result.returncode == 0, result.stderr
    output = tmp_path / "github-upload"
    assert {path.name for path in output.iterdir()} == {extra, current}, (
        "the new file needs its bundle while old subjects keep retained provenance"
    )
    assert (output / current).read_bytes() == (bundles / current).read_bytes(), (
        "the new bundle must retain its attested subject claims"
    )
    typ.cast("list[dict[str, str]]", gh_assets["assets"]).extend([
        {"name": extra, "digest": f"sha256:{digest(b'new wheel filename')}"},
        {
            "name": current,
            "digest": f"sha256:{digest((bundles / current).read_bytes())}",
        },
    ])
    result = run_github_step(
        tmp_path,
        "verify",
        {"GH_ASSETS_JSON": json.dumps(gh_assets), "PYPI_INDEX_PATH": "pypi-index.json"},
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("missing_subject", [True, False])
def test_uncovered_canonical_digest_blocks_pypi_and_github_before_staging(
    tmp_path: Path, *, missing_subject: bool
) -> None:
    """A retained bundle name cannot cover missing or mismatched wheel subjects."""
    prior = f"cmd-mox-{TAG}-run40-1.sigstore.json"
    old_sdist, old_wheel = b"canonical sdist", b"old wheel"
    subjects = {SDIST: old_sdist}
    if not missing_subject:
        subjects[WHEEL] = b"different wheel digest"
    publish, _ = write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets=build_github_assets(
                sdist=old_sdist, wheel=old_wheel, bundle=prior
            ),
            bundle_name=f"cmd-mox-{TAG}-run41-1.sigstore.json",
            wheel=b"rebuilt wheel",
            pypi_assets={SDIST: old_sdist},
            retained_subjects={prior: subjects},
        ),
    )
    script, _ = load_workflow_step("publish-pypi", "pending")
    pypi_result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "PYPI_INDEX_PATH": "pypi-index.json",
            "TAG_NAME": TAG,
        },
    )
    assert pypi_result.returncode == 1, (
        "uncovered carried bytes must fail before PyPI publication"
    )
    assert "has no matching provenance subject" in pypi_result.stderr, (
        "the error must identify subject-level coverage"
    )
    assert (publish / WHEEL).read_bytes() == b"rebuilt wheel", (
        "failed provenance checks must precede filesystem mutations"
    )
    github_result = run_github_step(
        tmp_path, "stage", {"PYPI_INDEX_PATH": "pypi-index.json"}
    )
    assert github_result.returncode == 1, (
        "uncovered carried bytes must also block GitHub staging"
    )
    assert not list((tmp_path / "github-upload").iterdir()), (
        "no assets may be staged after failed provenance validation"
    )
