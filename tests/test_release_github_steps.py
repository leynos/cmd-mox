"""Tests for draft reuse, GitHub assets and release publication.

Run ``make test`` or ``python -m pytest -q tests/test_release_github_steps.py``.
These tests execute the checked-in Bash steps and release reconciliation CLI
against recording fake tools.
"""

from __future__ import annotations

import json
import typing as typ

import pytest
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
from helpers.release_workflow import (
    install_fake_tools,
    load_workflow,
    load_workflow_step,
    provenance_bundle,
    read_tool_calls,
    run_bash_step,
)
from helpers.release_workflow import (
    release_shell_marks as pytestmark,
)

__all__ = ["pytestmark"]

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("release_state", ["draft", "published"])
def test_draft_step_reuses_existing_draft_or_published_release(
    tmp_path: Path, release_state: str
) -> None:
    """An existing release is reused regardless of whether it is draft."""
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    state_log = tmp_path / "release-state.txt"
    script, _ = load_workflow_step("draft-release", "create")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(log),
            "GH_RELEASE_EXISTS": "1",
            "GH_RELEASE_STATE": release_state,
            "GH_RELEASE_STATE_LOG": str(state_log),
            "IS_PRERELEASE": "false",
            "PATH": f"{tools}:/usr/bin:/bin",
            "TAG_NAME": TAG,
        },
    )

    calls = read_tool_calls(log, tool="gh")
    assert result.returncode == 0, result.stderr
    assert state_log.read_text(encoding="utf-8") == release_state, (
        "the fake GitHub CLI must expose the parametrized existing release state"
    )
    assert calls == [{"tool": "gh", "argv": ["release", "view", TAG]}], (
        f"the {release_state} release must be reused without create"
    )


@pytest.mark.parametrize(
    ("prerelease", "expected_flags"),
    [("true", ["--prerelease"]), ("false", [])],
)
def test_draft_step_creates_after_view_failure_with_exact_argv(
    tmp_path: Path, prerelease: str, expected_flags: list[str]
) -> None:
    """A failed view creates a draft and quotes a shell-sensitive tag literally."""
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    marker = "injected-tag-command-must-not-run"
    tag = f"v0.3.0-beta1;$(touch {marker})"
    script, _ = load_workflow_step("draft-release", "create")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(log),
            "GH_RELEASE_EXISTS": "0",
            "IS_PRERELEASE": prerelease,
            "PATH": f"{tools}:/usr/bin:/bin",
            "TAG_NAME": tag,
        },
    )

    calls = read_tool_calls(log, tool="gh")
    expected_create = [
        "release",
        "create",
        tag,
        "--draft",
        "--verify-tag",
        "--generate-notes",
        "--title",
        tag,
        *expected_flags,
    ]
    assert result.returncode == 0, result.stderr
    assert calls == [
        gh_call(["release", "view", tag]),
        gh_call(expected_create),
    ], "gh must receive exact argv with the literal tag and prerelease flag"
    assert not (tmp_path / marker).exists(), "tag text must not execute as shell code"


def test_snapshot_downloads_only_assets_carried_over_from_github(
    tmp_path: Path,
) -> None:
    """The snapshot script downloads only filenames shared with local output."""
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    publish = tmp_path / "dist" / "publish"
    downloads = tmp_path / "downloads"
    publish.mkdir(parents=True)
    downloads.mkdir()
    (publish / SDIST).write_bytes(b"new sdist")
    (publish / WHEEL).write_bytes(b"new wheel")
    (downloads / WHEEL).write_bytes(b"old wheel")
    bundles = tmp_path / "dist" / "attestations"
    bundles.mkdir()
    prior_bundle = f"cmd-mox-{TAG}-run1-1.sigstore.json"
    (bundles / f"cmd-mox-{TAG}-run2-1.sigstore.json").write_bytes(
        provenance_bundle({SDIST: b"new sdist", WHEEL: b"new wheel"})
    )
    (downloads / prior_bundle).write_bytes(provenance_bundle({WHEEL: b"old wheel"}))
    script, _ = load_workflow_step("draft-release", "snapshot")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(log),
            "GH_ASSETS_JSON": json.dumps({
                "assets": [{"name": WHEEL}, {"name": prior_bundle}]
            }),
            "GH_DOWNLOADS": str(downloads),
            "GH_RELEASE_EXISTS": "1",
            "PATH": f"{tools}:/usr/bin:/bin",
            "TAG_NAME": TAG,
        },
    )

    calls = read_tool_calls(log, tool="gh")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "github-state" / "carryover.txt").read_text(
        encoding="utf-8"
    ) == f"{prior_bundle}\n{WHEEL}\n", (
        "carry-over must retain subjects as well as canonical distribution bytes"
    )
    assert (tmp_path / "github-state" / "files" / prior_bundle).read_bytes() == (
        downloads / prior_bundle
    ).read_bytes(), "retained bundle bytes must reach later release jobs"
    assert (tmp_path / "github-state" / "files" / WHEEL).read_bytes() == b"old wheel", (
        "the existing GitHub bytes must be staged for PyPI reconciliation"
    )
    assert calls[-1]["argv"] == [
        "release",
        "download",
        TAG,
        "--pattern",
        WHEEL,
        "--dir",
        "github-state/files",
    ], "the workflow must download the exact asset selected by the CLI"


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
    script, _ = load_workflow_step("publish-release", "stage")
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(log),
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
            "TAG_NAME": TAG,
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


@pytest.mark.parametrize("failure", ["digest", "bundle"])
def test_verify_rejects_digest_mismatches_and_missing_tag_bundle(
    tmp_path: Path, failure: str
) -> None:
    """Verification blocks publication for changed bytes or absent provenance."""
    bundle = f"cmd-mox-{TAG}-run51-1.sigstore.json"
    assets = build_github_assets(
        sdist=b"canonical sdist",
        wheel=b"different wheel" if failure == "digest" else b"canonical wheel",
        bundle=bundle if failure == "digest" else None,
    )
    write_publication_inputs(
        tmp_path,
        PublicationInputs(github_assets=assets, bundle_name=bundle),
    )
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    verify_script, _ = load_workflow_step("publish-release", "verify")

    result = run_bash_step(
        verify_script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(log),
            "GH_ASSETS_JSON": json.dumps(assets),
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": "pypi-index.json",
            "TAG_NAME": TAG,
        },
    )

    assert result.returncode == 1, "a digest or provenance mismatch must fail"
    expected = (
        "differs" if failure == "digest" else "has no matching provenance subject"
    )
    assert expected in result.stderr, "verification must explain its failing invariant"
    calls = read_tool_calls(log, tool="gh")
    commands = [typ.cast("list[str]", call["argv"]) for call in calls]
    assert not any(argv[:2] == ["release", "edit"] for argv in commands), (
        "a failed verification must not execute the release edit"
    )


def test_successful_verification_runs_release_edit_last_with_prerelease_flag(
    tmp_path: Path,
) -> None:
    """The release is made visible only after matching digests are verified."""
    assets = build_github_assets(
        sdist=b"canonical sdist",
        wheel=b"canonical wheel",
        bundle=f"cmd-mox-{TAG}-run60-1.sigstore.json",
    )
    write_publication_inputs(
        tmp_path,
        PublicationInputs(
            github_assets=assets,
            bundle_name=f"cmd-mox-{TAG}-run61-1.sigstore.json",
        ),
    )
    verify_result = run_github_step(
        tmp_path,
        "verify",
        {
            "GH_ASSETS_JSON": json.dumps(assets),
            "PYPI_INDEX_PATH": "pypi-index.json",
        },
    )
    publish_result = run_github_step(tmp_path, "publish", {"IS_PRERELEASE": "true"})

    assert verify_result.returncode == 0, verify_result.stderr
    assert "hold the same bytes" in verify_result.stdout, (
        "matching destinations must emit a success message"
    )
    assert publish_result.returncode == 0, publish_result.stderr
    assert [
        step.get("id") for step in load_workflow()["jobs"]["publish-release"]["steps"]
    ].index("verify") < [
        step.get("id") for step in load_workflow()["jobs"]["publish-release"]["steps"]
    ].index("publish"), "the workflow must verify before editing the release state"
    assert read_tool_calls(tmp_path / "tool-calls.jsonl", tool="gh")[-1] == gh_call([
        "release",
        "edit",
        TAG,
        "--draft=false",
        "--prerelease=true",
    ]), "the final GitHub operation must publish with the version's prerelease flag"


@pytest.mark.parametrize(("second_view", "expected_status"), [("0", 0), ("1", 1)])
def test_failed_release_creation_rechecks_existing_release(
    tmp_path: Path, second_view: str, expected_status: int
) -> None:
    """A transient lookup cannot make an existing release fail a safe retry."""
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    script, _ = load_workflow_step("draft-release", "create")
    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(log),
            "GH_VIEW_STATUSES": f"1,{second_view}",
            "GH_CREATE_STATUS": "1",
            "IS_PRERELEASE": "false",
            "PATH": f"{tools}:/usr/bin:/bin",
            "TAG_NAME": TAG,
        },
    )
    assert result.returncode == expected_status, (
        "only a successful recovery lookup may continue"
    )
    expected_calls = [
        gh_call(["release", "view", TAG]),
        gh_call([
            "release",
            "create",
            TAG,
            "--draft",
            "--verify-tag",
            "--generate-notes",
            "--title",
            TAG,
        ]),
        gh_call(["release", "view", TAG]),
    ]
    assert read_tool_calls(log, tool="gh") == expected_calls, (
        "a failed create must be followed by exactly one quoted recovery lookup"
    )
