"""Tests for retrying release jobs after partial asset publication."""

from __future__ import annotations

import json
import shutil
import typing as typ

from helpers.release_github import (
    SDIST,
    TAG,
    WHEEL,
    PublicationInputs,
    build_github_assets,
    run_github_step,
    write_publication_inputs,
)
from helpers.release_workflow import (
    install_fake_tools,
    load_workflow_step,
    read_tool_calls,
    run_bash_step,
)
from helpers.release_workflow import (
    release_shell_marks as pytestmark,
)

__all__ = ["pytestmark"]

if typ.TYPE_CHECKING:
    from pathlib import Path


def _prepare_partial_retry(tmp_path: Path) -> tuple[dict[str, bytes], dict[str, str]]:
    """Prepare a rerun where PyPI already contains the original sdist.

    Returns
    -------
    tuple[dict[str, bytes], dict[str, str]]
        Original distributions and the shell environment for workflow steps.
    """
    prior_bundle = f"cmd-mox-{TAG}-run40-1.sigstore.json"
    original = {SDIST: b"first build sdist", WHEEL: b"first build wheel"}
    assets = build_github_assets(
        sdist=original[SDIST], wheel=original[WHEEL], bundle=prior_bundle
    )
    write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets=assets,
            bundle_name=f"cmd-mox-{TAG}-run41-1.sigstore.json",
            sdist=b"retry build sdist",
            wheel=b"retry build wheel",
            pypi_assets={SDIST: original[SDIST]},
            retained_subjects={prior_bundle: original},
        ),
    )
    github_downloads = tmp_path / "github-downloads"
    github_downloads.mkdir()
    for name, content in original.items():
        (github_downloads / name).write_bytes(content)
    shutil.copyfile(
        tmp_path / "github-state" / "files" / prior_bundle,
        github_downloads / prior_bundle,
    )
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    common_env = {
        "CURL_BODY": (tmp_path / "pypi-index.json").read_text(encoding="utf-8"),
        "FAKE_TOOL_LOG": str(log),
        "GH_ASSETS_JSON": json.dumps(assets),
        "GH_DOWNLOADS": str(github_downloads),
        "PATH": f"{tools}:/usr/bin:/bin",
        "PYPI_INDEX_PATH": "pypi-index.json",
        "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        "TAG_NAME": TAG,
    }
    return original, common_env


def test_failed_job_rerun_refreshes_missing_github_carryover(
    tmp_path: Path,
) -> None:
    """A rerun downloads carried assets absent from its old snapshot."""
    prior_bundle = f"cmd-mox-{TAG}-run40-1.sigstore.json"
    current_bundle = f"cmd-mox-{TAG}-run41-1.sigstore.json"
    published = {
        SDIST: b"PyPI sdist from the earlier attempt",
        WHEEL: b"PyPI wheel from the earlier attempt",
    }
    release_assets = build_github_assets(
        sdist=published[SDIST], wheel=published[WHEEL], bundle=prior_bundle
    )
    write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets=release_assets,
            bundle_name=current_bundle,
            sdist=b"sdist rebuilt by failed-job rerun",
            wheel=b"wheel rebuilt by failed-job rerun",
            pypi_assets=published,
            retained_subjects={prior_bundle: published},
        ),
    )
    cached_files = tmp_path / "github-state" / "files"
    (cached_files / SDIST).unlink()
    (cached_files / WHEEL).unlink()
    downloads = tmp_path / "github-downloads"
    downloads.mkdir()
    (downloads / SDIST).write_bytes(published[SDIST])
    (downloads / WHEEL).write_bytes(published[WHEEL])

    result = run_github_step(
        tmp_path,
        "stage",
        {
            "GH_ASSETS_JSON": json.dumps(release_assets),
            "GH_DOWNLOADS": str(downloads),
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    assert result.returncode == 0, result.stderr
    assert (cached_files / SDIST).read_bytes() == published[SDIST], (
        "the current GitHub inventory must refresh its missing sdist bytes"
    )
    assert (cached_files / WHEEL).read_bytes() == published[WHEEL], (
        "the current GitHub inventory must refresh its missing wheel bytes"
    )
    download_calls = [
        call
        for call in read_tool_calls(tmp_path / "tool-calls.jsonl", tool="gh")
        if typ.cast("list[str]", call["argv"])[0:2] == ["release", "download"]
    ]
    assert [call["argv"] for call in download_calls] == [
        ["release", "download", TAG, "--pattern", name, "--dir", "github-state/files"]
        for name in sorted((SDIST, WHEEL))
    ], "the rerun must download missing assets but preserve the cached bundle"
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == (
        "pending=false\n"
    ), "the already complete release must not upload duplicate assets"


def test_full_retry_reuses_the_original_pair_after_partial_pypi_upload(
    tmp_path: Path,
) -> None:
    """A retry stages the retained wheel that matches the original sdist."""
    original, common_env = _prepare_partial_retry(tmp_path)
    index_result = run_bash_step(
        load_workflow_step("draft-release", "index")[0],
        cwd=tmp_path,
        env=common_env,
        output_path=tmp_path / "index-output",
    )
    assert index_result.returncode == 0, index_result.stderr

    seed_result = run_bash_step(
        load_workflow_step("draft-release", "seed")[0],
        cwd=tmp_path,
        env=common_env,
        output_path=tmp_path / "draft-seed-output",
    )
    assert seed_result.returncode == 0, seed_result.stderr
    assert (tmp_path / "draft-seed-output").read_text(encoding="utf-8") == (
        "pending=false\n"
    ), "the retry must keep the complete original pair already on the draft"
    assert not list((tmp_path / "github-upload").iterdir()), (
        "the retry must not try to upload rebuilt bytes under existing filenames"
    )

    pypi_result = run_bash_step(
        load_workflow_step("publish-pypi", "pending")[0],
        cwd=tmp_path,
        env=common_env,
        output_path=tmp_path / "pypi-pending-output",
    )
    assert pypi_result.returncode == 0, pypi_result.stderr
    assert not (tmp_path / "dist" / "publish" / SDIST).exists(), (
        "PyPI's existing sdist is not re-uploaded"
    )
    assert (tmp_path / "dist" / "publish" / WHEEL).read_bytes() == original[WHEEL], (
        "the missing wheel must come from the same build as the published sdist"
    )
    assert (
        tmp_path / "dist" / "publish" / WHEEL
    ).read_bytes() != b"retry build wheel", (
        "a new full-run wheel must never be paired with the retained sdist"
    )
    assert (tmp_path / "pypi-pending-output").read_text(encoding="utf-8") == (
        "remaining=true\n"
    ), "only the original wheel remains for Trusted Publishing"
