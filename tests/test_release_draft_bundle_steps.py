"""Draft-release tests for retaining the complete attested distribution set."""

from __future__ import annotations

import typing as typ

from helpers.release_github import SDIST, TAG, WHEEL
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
    import subprocess
    from pathlib import Path


def _seed_release(
    tmp_path: Path, *, include_bundle: bool = True
) -> tuple[subprocess.CompletedProcess[str], list[dict[str, object]], str | None]:
    """Run the checked-in draft seed step with a fresh attested pair.

    Returns
    -------
    tuple[subprocess.CompletedProcess[str], list[dict[str, object]], str | None]
        Seed result, recorded GitHub calls, and the current bundle name.
    """
    publish = tmp_path / "dist" / "publish"
    bundles = tmp_path / "dist" / "attestations"
    publish.mkdir(parents=True)
    bundles.mkdir(parents=True)
    distributions = {
        SDIST: b"attested source archive",
        WHEEL: b"attested universal wheel",
    }
    for name, content in distributions.items():
        (publish / name).write_bytes(content)
    bundle_name = f"cmd-mox-{TAG}-run73-2.sigstore.json"
    if include_bundle:
        (bundles / bundle_name).write_bytes(provenance_bundle(distributions))

    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    _fetch_seed_index(tmp_path, tools, log)
    result = run_bash_step(
        load_workflow_step("draft-release", "seed")[0],
        cwd=tmp_path,
        env={
            "CURL_BODY": '{"files": []}',
            "FAKE_TOOL_LOG": str(log),
            "GH_ASSETS_JSON": '{"assets": []}',
            "GH_RELEASE_EXISTS": "1",
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
            "TAG_NAME": TAG,
        },
    )
    return (
        result,
        read_tool_calls(log, tool="gh"),
        bundle_name if include_bundle else None,
    )


def _fetch_seed_index(tmp_path: Path, tools: Path, log: Path) -> None:
    """Create the empty PyPI inventory needed by a fresh draft seed."""
    result = run_bash_step(
        load_workflow_step("draft-release", "index")[0],
        cwd=tmp_path,
        env={
            "CURL_BODY": '{"files": []}',
            "FAKE_TOOL_LOG": str(log),
            "PATH": f"{tools}:/usr/bin:/bin",
            "PYPI_INDEX_PATH": "pypi-index.json",
            "PYPI_INDEX_URL": "https://pypi.org/simple/cmd-mox/",
        },
        output_path=tmp_path / "index-output",
    )
    assert result.returncode == 0, result.stderr


def test_draft_seed_uploads_the_attested_pair_and_its_bundle(tmp_path: Path) -> None:
    """The draft retains the entire build before PyPI publication can start."""
    result, _, bundle_name = _seed_release(tmp_path)

    assert result.returncode == 0, result.stderr
    staged_names = {path.name for path in (tmp_path / "github-upload").iterdir()}
    assert staged_names == {SDIST, WHEEL, bundle_name}, (
        "the draft seed must contain both distributions and their bundle"
    )
    script, _ = load_workflow_step("draft-release", "seed-upload")
    upload = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(tmp_path / "tool-calls.jsonl"),
            "PATH": f"{tmp_path / 'bin'}:/usr/bin:/bin",
            "TAG_NAME": TAG,
        },
    )
    assert upload.returncode == 0, upload.stderr
    upload_calls = [
        typ.cast("list[str]", call["argv"])
        for call in read_tool_calls(tmp_path / "tool-calls.jsonl", tool="gh")
        if typ.cast("list[str]", call["argv"])[0:2] == ["release", "upload"]
    ]
    assert len(upload_calls) == 1, "the complete seed must be attached in one call"
    assert set(upload_calls[0][2:]) == {
        TAG,
        *(f"github-upload/{name}" for name in staged_names),
    }, "the draft upload must carry exactly the attested pair and bundle"
    assert all("--clobber" not in call for call in upload_calls), (
        "draft seeding must never overwrite a release asset"
    )

    steps = load_workflow()["jobs"]["draft-release"]["steps"]
    step_ids = [step.get("id") for step in steps]
    assert (
        step_ids.index("seed")
        < step_ids.index("seed-upload")
        < step_ids.index("snapshot")
    ), "the release-state handoff must snapshot the seeded draft"


def test_draft_seed_fails_before_upload_without_a_provenance_bundle(
    tmp_path: Path,
) -> None:
    """A draft cannot persist distribution files that have no attestation."""
    result, calls, _ = _seed_release(tmp_path, include_bundle=False)

    assert result.returncode == 1, "the draft seed must fail closed without a bundle"
    assert "has no matching provenance subject" in result.stderr, (
        "the missing attestation must be reported"
    )
    assert not (tmp_path / "github-output").exists(), (
        "failed provenance validation must not mark assets pending"
    )
    assert not any(
        typ.cast("list[str]", call["argv"])[0:2] == ["release", "upload"]
        for call in calls
    ), "no draft asset upload may precede provenance validation"
