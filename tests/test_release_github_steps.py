"""Tests for draft reuse, GitHub assets and release publication.

Run with ``make test`` or
``python -m pytest -q tests/test_release_github_steps.py``. These tests execute
the checked-in Bash steps and reconciliation CLI against recording fake tools.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import typing as typ
from pathlib import Path

import pytest
from helpers.release_workflow import (
    RELEASE_WORKFLOW_PATH,
    install_fake_tools,
    load_workflow,
    load_workflow_step,
    read_tool_calls,
    run_bash_step,
    should_skip_release_tests_for_mutation_sandbox,
)

if typ.TYPE_CHECKING:
    import subprocess

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

_TAG = "v0.3.0-beta1"
_SDIST = "cmd_mox-0.3.0.tar.gz"
_WHEEL = "cmd_mox-0.3.0-py3-none-any.whl"


def _write_json(path: Path, document: object) -> None:
    """Write a JSON document with stable UTF-8 encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _digest(content: bytes) -> str:
    """Return a lowercase SHA-256 digest for test asset content.

    Returns
    -------
    str
        SHA-256 hexadecimal digest.
    """
    return hashlib.sha256(content).hexdigest()


def _gh_call(argv: list[str]) -> dict[str, object]:
    """Return one expected recording-stub call for the GitHub CLI.

    Returns
    -------
    dict[str, object]
        Tool name and exact argument vector in the fake-tool log format.
    """
    return {"tool": "gh", "argv": argv}


def _assets(
    *, sdist: bytes, wheel: bytes, bundle: str | None = None
) -> dict[str, object]:
    """Return a GitHub API asset response for a release's distributions.

    Returns
    -------
    dict[str, object]
        GitHub assets with digests and an optional provenance bundle.
    """
    items: list[dict[str, str]] = [
        {"name": _SDIST, "digest": f"sha256:{_digest(sdist)}"},
        {"name": _WHEEL, "digest": f"sha256:{_digest(wheel)}"},
    ]
    if bundle is not None:
        items.append({"name": bundle, "digest": "sha256:bundle-digest"})
    return {"assets": items}


def _run_github_step(
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
            "TAG_NAME": _TAG,
            **extra_env,
        },
    )


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
            "TAG_NAME": _TAG,
        },
    )

    calls = read_tool_calls(log, tool="gh")
    assert result.returncode == 0, result.stderr
    assert state_log.read_text(encoding="utf-8") == release_state, (
        "the fake GitHub CLI must expose the parametrized existing release state"
    )
    assert calls == [{"tool": "gh", "argv": ["release", "view", _TAG]}], (
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
        _gh_call(["release", "view", tag]),
        _gh_call(expected_create),
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
    (publish / _SDIST).write_bytes(b"new sdist")
    (publish / _WHEEL).write_bytes(b"new wheel")
    (downloads / _WHEEL).write_bytes(b"old wheel")
    script, _ = load_workflow_step("draft-release", "snapshot")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(log),
            "GH_ASSETS_JSON": json.dumps({"assets": [{"name": _WHEEL}]}),
            "GH_DOWNLOADS": str(downloads),
            "GH_RELEASE_EXISTS": "1",
            "PATH": f"{tools}:/usr/bin:/bin",
            "TAG_NAME": _TAG,
        },
    )

    calls = read_tool_calls(log, tool="gh")
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "github-state" / "carryover.txt").read_text(
        encoding="utf-8"
    ) == f"{_WHEEL}\n", "only overlapping GitHub asset names need carry-over"
    assert (
        tmp_path / "github-state" / "files" / _WHEEL
    ).read_bytes() == b"old wheel", (
        "the existing GitHub bytes must be staged for PyPI reconciliation"
    )
    assert calls[-1]["argv"] == [
        "release",
        "download",
        _TAG,
        "--pattern",
        _WHEEL,
        "--dir",
        "github-state/files",
    ], "the workflow must download the exact asset selected by the CLI"


def _write_publication_inputs(
    root: Path,
    *,
    github_assets: dict[str, object],
    bundle_name: str,
    sdist: bytes = b"canonical sdist",
    wheel: bytes = b"canonical wheel",
    pypi_assets: dict[str, bytes] | None = None,
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
    (publish / _SDIST).write_bytes(sdist)
    (publish / _WHEEL).write_bytes(wheel)
    (bundles / bundle_name).write_bytes(b"current run provenance")
    built_assets = {_SDIST: sdist, _WHEEL: wheel}
    index_assets = built_assets if pypi_assets is None else pypi_assets
    index = {
        "files": [
            {
                "filename": name,
                "hashes": {"sha256": _digest(content)},
                "url": f"https://files.example.invalid/{name}",
            }
            for name, content in index_assets.items()
        ]
    }
    _write_json(root / "pypi-index.json", index)
    _write_json(root / "release-assets.json", github_assets)
    return publish, bundles


def test_stage_github_stages_local_assets_and_attestation_bundle(
    tmp_path: Path,
) -> None:
    """GitHub gets this run's assets and bundle when no destination has them."""
    current_bundle = f"cmd-mox-{_TAG}-run41-1.sigstore.json"
    _write_publication_inputs(
        tmp_path,
        github_assets={"assets": []},
        bundle_name=current_bundle,
        pypi_assets={},
    )
    result = _run_github_step(
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
        _SDIST,
        _WHEEL,
        current_bundle,
    }, "local canonical distributions must be staged with their bundle"
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == (
        "pending=true\n"
    ), "missing release assets must enable the upload step"
    upload_result = _run_github_step(tmp_path, "upload", {})
    assert upload_result.returncode == 0, upload_result.stderr
    assert read_tool_calls(tmp_path / "tool-calls.jsonl", tool="gh")[-1] == _gh_call([
        "release",
        "upload",
        _TAG,
        *sorted([
            f"github-upload/{_SDIST}",
            f"github-upload/{_WHEEL}",
            f"github-upload/{current_bundle}",
        ]),
    ]), "the release upload must include only staged files and no clobber flag"


def test_pypi_sourced_rerun_keeps_existing_bundle_and_bytes(tmp_path: Path) -> None:
    """A rerun uses PyPI's bytes without attaching a bundle for rebuilt files."""
    prior_bundle = f"cmd-mox-{_TAG}-run40-1.sigstore.json"
    current_bundle = f"cmd-mox-{_TAG}-run41-1.sigstore.json"
    pypi_sdist = b"previously published sdist"
    pypi_wheel = b"previously published wheel"
    curl_downloads = tmp_path / "curl-downloads"
    curl_downloads.mkdir()
    (curl_downloads / _SDIST).write_bytes(pypi_sdist)
    (curl_downloads / _WHEEL).write_bytes(pypi_wheel)
    _write_publication_inputs(
        tmp_path,
        github_assets={"assets": [{"name": prior_bundle}]},
        bundle_name=current_bundle,
        pypi_assets={_SDIST: pypi_sdist, _WHEEL: pypi_wheel},
    )

    result = _run_github_step(
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
    assert {path.name for path in output.iterdir()} == {_SDIST, _WHEEL}, (
        "PyPI carry-overs must be staged without this run's unrelated bundle"
    )
    assert (output / _SDIST).read_bytes() == pypi_sdist, (
        "GitHub must receive PyPI's canonical sdist bytes"
    )
    assert (output / _WHEEL).read_bytes() == pypi_wheel, (
        "GitHub must receive PyPI's canonical wheel bytes"
    )
    assert (tmp_path / "github-output").read_text(encoding="utf-8") == (
        "pending=true\n"
    ), "staging missing GitHub assets must enable the upload step"

    assets = _assets(sdist=pypi_sdist, wheel=pypi_wheel, bundle=prior_bundle)
    verify_result = _run_github_step(
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
    bundle = f"cmd-mox-{_TAG}-run41-1.sigstore.json"
    curl_downloads = tmp_path / "curl-downloads"
    curl_downloads.mkdir()
    (curl_downloads / _SDIST).write_bytes(b"canonical sdist")
    (curl_downloads / _WHEEL).write_bytes(b"canonical wheel")
    _write_publication_inputs(
        tmp_path,
        github_assets={"assets": []},
        bundle_name=bundle,
        pypi_assets={
            _SDIST: b"canonical sdist",
            _WHEEL: b"canonical wheel",
        },
    )

    result = _run_github_step(
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
        _SDIST,
        _WHEEL,
        bundle,
    }, "matching PyPI and attested bytes must carry the current run's bundle"
    assert (output / _SDIST).read_bytes() == b"canonical sdist", (
        "the staged sdist must match the attested build"
    )
    assert (output / _WHEEL).read_bytes() == b"canonical wheel", (
        "the staged wheel must match the attested build"
    )


def test_stage_rejects_carried_assets_without_retained_provenance(
    tmp_path: Path,
) -> None:
    """The workflow stops before uploading carried bytes without their bundle."""
    current_bundle = f"cmd-mox-{_TAG}-run41-1.sigstore.json"
    published_sdist = b"previously published sdist"
    published_wheel = b"previously published wheel"
    curl_downloads = tmp_path / "curl-downloads"
    curl_downloads.mkdir()
    (curl_downloads / _SDIST).write_bytes(published_sdist)
    (curl_downloads / _WHEEL).write_bytes(published_wheel)
    _write_publication_inputs(
        tmp_path,
        github_assets={"assets": []},
        bundle_name=current_bundle,
        pypi_assets={_SDIST: published_sdist, _WHEEL: published_wheel},
    )

    result = _run_github_step(
        tmp_path,
        "stage",
        {
            "CURL_DOWNLOADS": str(curl_downloads),
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
    )

    assert result.returncode == 1, "carried bytes without retained provenance must fail"
    assert "carried-over release assets have no retained provenance bundle" in (
        result.stderr
    ), "the error must identify missing retained provenance"
    assert not (tmp_path / "github-upload").exists(), (
        "no release assets should be staged when no matching bundle is available"
    )


def test_stage_github_is_empty_when_assets_and_tag_bundle_are_complete(
    tmp_path: Path,
) -> None:
    """A complete tag has no pending upload, including on a workflow rerun."""
    bundle = f"cmd-mox-{_TAG}-run41-1.sigstore.json"
    prior_bundle = f"cmd-mox-{_TAG}-run40-1.sigstore.json"
    assets = _assets(
        sdist=b"canonical sdist", wheel=b"canonical wheel", bundle=prior_bundle
    )
    # A unique run name must not force another bundle onto a complete release.
    (publish, _) = _write_publication_inputs(
        tmp_path,
        github_assets=assets,
        bundle_name=bundle,
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
            "TAG_NAME": _TAG,
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
    bundle = f"cmd-mox-{_TAG}-run51-1.sigstore.json"
    assets = _assets(
        sdist=b"canonical sdist",
        wheel=b"different wheel" if failure == "digest" else b"canonical wheel",
        bundle=bundle if failure == "digest" else None,
    )
    _write_publication_inputs(
        tmp_path,
        github_assets=assets,
        bundle_name=bundle,
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
            "TAG_NAME": _TAG,
        },
    )

    assert result.returncode == 1, "a digest or provenance mismatch must fail"
    expected = (
        "differs"
        if failure == "digest"
        else f"{bundle} is missing from the GitHub Release"
    )
    assert expected in result.stderr, "verification must explain its failing invariant"
    assert len(read_tool_calls(log, tool="gh")) == 1, (
        "a failed verification must not execute the release edit"
    )


def test_successful_verification_runs_release_edit_last_with_prerelease_flag(
    tmp_path: Path,
) -> None:
    """The release is made visible only after matching digests are verified."""
    assets = _assets(
        sdist=b"canonical sdist",
        wheel=b"canonical wheel",
        bundle=f"cmd-mox-{_TAG}-run60-1.sigstore.json",
    )
    _write_publication_inputs(
        tmp_path,
        github_assets=assets,
        bundle_name=f"cmd-mox-{_TAG}-run61-1.sigstore.json",
    )
    verify_result = _run_github_step(
        tmp_path,
        "verify",
        {
            "GH_ASSETS_JSON": json.dumps(assets),
            "PYPI_INDEX_PATH": "pypi-index.json",
        },
    )
    publish_result = _run_github_step(tmp_path, "publish", {"IS_PRERELEASE": "true"})

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
    assert read_tool_calls(tmp_path / "tool-calls.jsonl", tool="gh")[-1] == _gh_call([
        "release",
        "edit",
        _TAG,
        "--draft=false",
        "--prerelease=true",
    ]), "the final GitHub operation must publish with the version's prerelease flag"
