"""Tests for GitHub release digest verification and publish ordering."""

from __future__ import annotations

import json
import typing as typ

import pytest
from helpers.release_github import (
    TAG,
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
    read_tool_calls,
    run_bash_step,
)
from helpers.release_workflow import (
    release_shell_marks as pytestmark,
)

__all__ = ["pytestmark"]

if typ.TYPE_CHECKING:
    from pathlib import Path


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
