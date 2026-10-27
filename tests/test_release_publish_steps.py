"""Tests for PyPI staging and idempotent release publication.

Run with ``make test`` or
``python -m pytest -q tests/test_release_publish_steps.py``. The tests execute
the checked-in scripts and workflow shell steps with temporary fake tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import sys
import typing as typ
from pathlib import Path

import pytest
import yaml
from helpers.release_github import TAG
from helpers.release_publish import _write_index
from helpers.release_workflow import (
    RELEASE_WORKFLOW_PATH,
    install_fake_tools,
    load_workflow_step,
    provenance_bundle,
    read_tool_calls,
    run_bash_step,
    should_skip_release_tests_for_mutation_sandbox,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_RELEASE_ASSETS_CLI = _REPOSITORY_ROOT / "scripts" / "release_assets_cli.py"
if should_skip_release_tests_for_mutation_sandbox(
    Path.cwd(), RELEASE_WORKFLOW_PATH, _RELEASE_ASSETS_CLI
):
    pytest.skip(
        "mutmut sandbox omits the release workflow and CLI", allow_module_level=True
    )


class _PypiStageFixture(typ.NamedTuple):
    """Filesystem paths and names for one mixed-source publication case."""

    publish: Path
    published_name: str
    carried_name: str
    local_name: str
    index: Path
    curl_downloads: Path


def _write_pypi_stage_fixture(root: Path) -> _PypiStageFixture:
    """Create local, PyPI, and retained GitHub inputs for a staging test.

    Returns
    -------
    _PypiStageFixture
        Paths and filenames used by the mixed-source staging case.
    """
    publish = root / "dist" / "publish"
    github_files = root / "github-state" / "files"
    publish.mkdir(parents=True)
    github_files.mkdir(parents=True)
    published_name = "cmd_mox-0.3.0.tar.gz"
    carried_name = "cmd_mox-0.3.0-py3-none-any.whl"
    local_name = "cmd_mox-0.3.0-extra.whl"
    (publish / published_name).write_bytes(b"rebuilt but not canonical")
    (publish / carried_name).write_bytes(b"new wheel bytes")
    (publish / local_name).write_bytes(b"new extra wheel")
    (github_files / carried_name).write_bytes(b"canonical GitHub bytes")
    _write_provenance(
        root / "dist" / "attestations",
        "cmd-mox-v0.3.0-run2-1.sigstore.json",
        {
            published_name: b"rebuilt but not canonical",
            carried_name: b"new wheel bytes",
            local_name: b"new extra wheel",
        },
    )
    index = root / "pypi-index.json"
    _write_index(index, [published_name])
    curl_downloads = root / "curl-downloads"
    curl_downloads.mkdir()
    (curl_downloads / published_name).write_bytes(published_name.encode())
    assets = root / "github-state" / "assets.json"
    assets.parent.mkdir(parents=True, exist_ok=True)
    _write_github_assets(
        assets,
        {
            carried_name: hashlib.sha256(b"canonical GitHub bytes").hexdigest(),
            "cmd-mox-v0.3.0-run1-1.sigstore.json": _write_provenance(
                github_files,
                "cmd-mox-v0.3.0-run1-1.sigstore.json",
                {
                    published_name: published_name.encode(),
                    carried_name: b"canonical GitHub bytes",
                },
            ),
        },
    )
    return _PypiStageFixture(
        publish, published_name, carried_name, local_name, index, curl_downloads
    )


pytestmark = [
    pytest.mark.skipif(
        os.name == "nt" or shutil.which("bash") is None,
        reason="release shell-step tests require Bash on a Unix-like system",
    ),
]


def _write_github_assets(path: Path, digests: dict[str, str]) -> None:
    """Write a GitHub asset listing with the provided SHA-256 values."""
    path.write_text(
        json.dumps({
            "assets": [
                {"name": name, "digest": f"sha256:{digest}"}
                for name, digest in digests.items()
            ]
        }),
        encoding="utf-8",
    )


def _write_provenance(directory: Path, name: str, subjects: dict[str, bytes]) -> str:
    """Write a bundle fixture and return its GitHub asset digest.

    Returns
    -------
    str
        SHA-256 digest of the retained bundle bytes.
    """
    directory.mkdir(parents=True, exist_ok=True)
    content = provenance_bundle(subjects)
    (directory / name).write_bytes(content)
    return hashlib.sha256(content).hexdigest()


def test_stage_pypi_skips_published_names_and_uses_github_bytes(tmp_path: Path) -> None:
    """Only names PyPI lacks remain, using existing GitHub bytes when needed."""
    fixture = _write_pypi_stage_fixture(tmp_path)
    script, _ = load_workflow_step("publish-pypi", "pending")

    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(log),
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": str(fixture.index),
            "TAG_NAME": TAG,
        },
    )

    assert result.returncode == 0, result.stderr
    assert not (fixture.publish / fixture.published_name).exists(), (
        "a filename already on PyPI must be removed from the upload directory"
    )
    assert (
        fixture.publish / fixture.carried_name
    ).read_bytes() == b"canonical GitHub bytes", (
        "PyPI must receive GitHub's canonical bytes for an existing asset"
    )
    assert (fixture.publish / fixture.local_name).read_bytes() == b"new extra wheel", (
        "an unpublished local distribution must remain staged"
    )
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == (
        "remaining=true\n"
    ), "the publish action must run when files remain"


def test_pypi_staging_stops_when_github_rejects_retained_attestation(
    tmp_path: Path,
) -> None:
    """A rejected retained bundle cannot authorize PyPI staging."""
    fixture = _write_pypi_stage_fixture(tmp_path)
    tools = install_fake_tools(tmp_path / "bin")
    script, _ = load_workflow_step("publish-pypi", "pending")
    log = tmp_path / "tool-calls.jsonl"
    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "CURL_DOWNLOADS": str(fixture.curl_downloads),
            "FAKE_TOOL_LOG": str(log),
            "GH_ATTESTATION_STATUS": "1",
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": str(fixture.index),
            "TAG_NAME": TAG,
        },
    )

    assert result.returncode != 0, "a failed GitHub signer check must stop publication"
    assert "attestation verification failed" in result.stderr, (
        "GitHub CLI's rejection must remain visible in the failed step"
    )
    assert (
        fixture.publish / fixture.carried_name
    ).read_bytes() == b"new wheel bytes", (
        "PyPI staging must not run after a retained attestation fails"
    )
    assert not (tmp_path / "github-output").exists(), (
        "a failed signer check must not emit the PyPI pending flag"
    )
    assert read_tool_calls(log, tool="gh"), (
        "the workflow must invoke GitHub attestation verification"
    )


def test_historical_pypi_only_release_is_noop_and_skips_upload(
    tmp_path: Path,
) -> None:
    """Re-tagging historical 0.2.0 skips PyPI even without retained provenance."""
    publish = tmp_path / "dist" / "publish"
    publish.mkdir(parents=True)
    (tmp_path / "github-state" / "files").mkdir(parents=True)
    (tmp_path / "dist" / "attestations").mkdir()
    names = ["cmd_mox-0.2.0.tar.gz", "cmd_mox-0.2.0-py3-none-any.whl"]
    for name in names:
        (publish / name).write_bytes(b"rebuilt historical bytes: " + name.encode())
    index = tmp_path / "pypi-index.json"
    _write_index(index, names)
    assets = tmp_path / "github-state" / "assets.json"
    assets.parent.mkdir(parents=True, exist_ok=True)
    _write_github_assets(assets, {})
    output = tmp_path / "github-output"
    output.touch()
    script, _ = load_workflow_step("publish-pypi", "pending")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "PATH": "/usr/bin:/bin",
            "PYPI_INDEX_PATH": str(index),
            "TAG_NAME": TAG,
        },
        output_path=output,
    )
    publish_job = yaml.safe_load(RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8"))[
        "jobs"
    ]["publish-pypi"]
    publish_step = next(
        step
        for step in publish_job["steps"]
        if step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
    )

    assert result.returncode == 0, result.stderr
    assert output.read_text(encoding="utf-8") == "remaining=false\n", (
        "the CLI must report that no PyPI upload remains"
    )
    assert publish_step["if"] == "steps.pending.outputs.remaining == 'true'", (
        "the PyPI action condition must skip an empty upload"
    )


def test_stage_pypi_requires_the_simple_index(tmp_path: Path) -> None:
    """A command that reconciles PyPI names cannot run without its index."""
    publish = tmp_path / "dist" / "publish"
    publish.mkdir(parents=True)
    assets = tmp_path / "assets.json"
    _write_github_assets(assets, {})
    command = shlex.join([
        sys.executable,
        "-m",
        "scripts.release_assets_cli",
        "stage-pypi",
        "--publish",
        str(publish),
        "--assets",
        str(assets),
        "--github-files",
        str(tmp_path),
    ])
    result = run_bash_step(
        f"set -euo pipefail\n{command}",
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 1, "stage-pypi must require --index"
    assert "::error title=release-assets::--index is required" in result.stderr, (
        "missing required reconciliation inputs must be annotated"
    )
