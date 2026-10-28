"""Tests for guarded GitHub staging and complete-release no-op behaviour."""

from __future__ import annotations

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
    load_workflow,
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


def test_github_staging_stops_when_current_attestation_is_rejected(
    tmp_path: Path,
) -> None:
    """A rejected current-run bundle cannot authorize GitHub Release assets."""
    bundle = f"cmd-mox-{TAG}-run41-1.sigstore.json"
    write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets={"assets": []}, bundle_name=bundle, pypi_assets={}
        ),
    )
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    script, _ = load_workflow_step("publish-release", "stage")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(log),
            "GH_ATTESTATION_STATUS": "1",
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
            "TAG_NAME": TAG,
        },
    )

    assert result.returncode != 0, "a failed current bundle check must stop staging"
    assert "attestation verification failed" in result.stderr, (
        "GitHub CLI's rejection must remain visible in the failed step"
    )
    assert not list((tmp_path / "github-upload").iterdir()), (
        "no local assets or bundle may stage after signature verification fails"
    )
    assert not any(
        typ.cast("list[str]", call["argv"])[0:2] == ["release", "upload"]
        for call in read_tool_calls(log, tool="gh")
    ), "the release upload must not follow failed provenance verification"


def test_stage_rejects_carried_assets_without_retained_provenance(
    tmp_path: Path,
) -> None:
    """The workflow stops before uploading carried bytes without their bundle."""
    current_bundle = f"cmd-mox-{TAG}-run41-1.sigstore.json"
    published_sdist = b"previously published sdist"
    published_wheel = b"previously published wheel"
    curl_downloads = tmp_path / "curl-downloads"
    curl_downloads.mkdir()
    (curl_downloads / SDIST).write_bytes(published_sdist)
    (curl_downloads / WHEEL).write_bytes(published_wheel)
    write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets={"assets": []},
            bundle_name=current_bundle,
            pypi_assets={SDIST: published_sdist, WHEEL: published_wheel},
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

    assert result.returncode == 1, "carried bytes without retained provenance must fail"
    assert "has no matching provenance subject" in (result.stderr), (
        "the error must identify missing retained provenance"
    )
    staged_names = {path.name for path in (tmp_path / "github-upload").iterdir()}
    assert staged_names == {SDIST, WHEEL}, (
        "only digest-checked PyPI bytes may be fetched before provenance validation"
    )


def test_stage_github_is_empty_when_assets_and_tag_bundle_are_complete(
    tmp_path: Path,
) -> None:
    """A complete tag has no pending upload, including on a workflow rerun."""
    bundle = f"cmd-mox-{TAG}-run41-1.sigstore.json"
    prior_bundle = f"cmd-mox-{TAG}-run40-1.sigstore.json"
    assets = build_github_assets(
        sdist=b"canonical sdist", wheel=b"canonical wheel", bundle=prior_bundle
    )
    # A unique run name must not force another bundle onto a complete release.
    (publish, _) = write_publication_inputs(
        tmp_path,
        PublicationInputs(github_assets=assets, bundle_name=bundle),
    )
    assert publish.exists(), "the test distribution directory must be present"
    result = run_github_step(
        tmp_path,
        "stage",
        {
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    assert result.returncode == 0, result.stderr
    assert not list((tmp_path / "github-upload").iterdir()), (
        "a complete release must not stage a new upload"
    )
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == (
        "pending=false\n"
    ), "an existing tag-scoped bundle must satisfy provenance on reruns"
    publish_step = next(
        step
        for step in load_workflow()["jobs"]["publish-release"]["steps"]
        if step.get("id") == "upload"
    )
    assert publish_step["if"] == "steps.stage.outputs.pending == 'true'", (
        "GitHub upload must be gated on pending assets"
    )
