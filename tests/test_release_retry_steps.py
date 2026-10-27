"""Tests for retrying release jobs after partial asset publication."""

from __future__ import annotations

import json
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
    read_tool_calls,
)
from helpers.release_workflow import (
    release_shell_marks as pytestmark,
)

__all__ = ["pytestmark"]

if typ.TYPE_CHECKING:
    from pathlib import Path


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
