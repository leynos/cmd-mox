"""Release workflow tests for provenance carry-over and subject coverage."""

from __future__ import annotations

import json
import os
import shutil
import typing as typ
from pathlib import Path

import pytest
from helpers.release_github import (
    _SDIST,
    _TAG,
    _WHEEL,
    _assets,
    _digest,
    _PublicationInputs,
    _run_github_step,
    _write_publication_inputs,
)
from helpers.release_workflow import (
    RELEASE_WORKFLOW_PATH,
    load_workflow_step,
    provenance_bundle,
    run_bash_step,
    should_skip_release_tests_for_mutation_sandbox,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_ASSETS_CLI = _REPOSITORY_ROOT / "scripts" / "release_assets_cli.py"
pytestmark = [
    pytest.mark.skipif(
        should_skip_release_tests_for_mutation_sandbox(
            Path.cwd(), RELEASE_WORKFLOW_PATH, _RELEASE_ASSETS_CLI
        ),
        reason="mutmut sandbox omits the release workflow and CLI",
    ),
    pytest.mark.skipif(
        os.name == "nt" or shutil.which("bash") is None,
        reason="release shell-step tests require Bash on a Unix-like system",
    ),
]


def test_new_filename_retains_old_subjects_and_adds_its_matching_current_bundle(
    tmp_path: Path,
) -> None:
    """A same-version addition needs both the retained and current provenance."""
    prior = f"cmd-mox-{_TAG}-run40-1.sigstore.json"
    current = f"cmd-mox-{_TAG}-run41-1.sigstore.json"
    extra = "cmd_mox-0.3.0-extra-py3-none-any.whl"
    old = {_SDIST: b"canonical sdist", _WHEEL: b"old wheel"}
    gh_assets = _assets(sdist=old[_SDIST], wheel=old[_WHEEL], bundle=prior)
    publish, bundles = _write_publication_inputs(
        tmp_path,
        _PublicationInputs(
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
            _SDIST: b"canonical sdist",
            _WHEEL: b"rebuilt wheel",
            extra: b"new wheel filename",
        })
    )
    (tmp_path / "downloads").mkdir()
    (tmp_path / "downloads" / extra).write_bytes(b"new wheel filename")
    result = _run_github_step(
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
        {"name": extra, "digest": f"sha256:{_digest(b'new wheel filename')}"},
        {
            "name": current,
            "digest": f"sha256:{_digest((bundles / current).read_bytes())}",
        },
    ])
    result = _run_github_step(
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
    prior = f"cmd-mox-{_TAG}-run40-1.sigstore.json"
    old_sdist, old_wheel = b"canonical sdist", b"old wheel"
    subjects = {_SDIST: old_sdist}
    if not missing_subject:
        subjects[_WHEEL] = b"different wheel digest"
    publish, _ = _write_publication_inputs(
        tmp_path,
        _PublicationInputs(
            github_assets=_assets(sdist=old_sdist, wheel=old_wheel, bundle=prior),
            bundle_name=f"cmd-mox-{_TAG}-run41-1.sigstore.json",
            wheel=b"rebuilt wheel",
            pypi_assets={_SDIST: old_sdist},
            retained_subjects={prior: subjects},
        ),
    )
    script, _ = load_workflow_step("publish-pypi", "pending")
    pypi_result = run_bash_step(
        script,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin", "PYPI_INDEX_PATH": "pypi-index.json"},
    )
    assert pypi_result.returncode == 1, (
        "uncovered carried bytes must fail before PyPI publication"
    )
    assert "has no matching provenance subject" in pypi_result.stderr, (
        "the error must identify subject-level coverage"
    )
    assert (publish / _WHEEL).read_bytes() == b"rebuilt wheel", (
        "failed provenance checks must precede filesystem mutations"
    )
    github_result = _run_github_step(
        tmp_path, "stage", {"PYPI_INDEX_PATH": "pypi-index.json"}
    )
    assert github_result.returncode == 1, (
        "uncovered carried bytes must also block GitHub staging"
    )
    assert not (tmp_path / "github-upload").exists(), (
        "no assets may be staged after failed provenance validation"
    )
