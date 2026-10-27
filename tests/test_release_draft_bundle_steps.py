"""Draft-release tests for retaining the current run's provenance bundle."""

from __future__ import annotations

import json
import typing as typ

import pytest
from helpers.release_github import TAG, WHEEL, gh_call
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
    import subprocess
    from pathlib import Path


class _BundleAttachmentCase(typ.NamedTuple):
    """Inputs for one release and provenance-bundle inventory."""

    release_state: str
    bundle_state: str
    bundle_attempt: str | None = "2"
    extra_bundle_attempt: str | None = None


def _write_bundle_inventory(tmp_path: Path, case: _BundleAttachmentCase) -> str:
    """Create the bundle inventory carried by the release-dist artefact.

    Returns
    -------
    str
        Filename of the bundle representing the selected run attempt.
    """
    bundle_attempt = case.bundle_attempt or "2"
    bundle_name = f"cmd-mox-{TAG}-run73-{bundle_attempt}.sigstore.json"
    if case.bundle_attempt is not None:
        bundle_path = tmp_path / "dist" / "attestations" / bundle_name
        bundle_path.parent.mkdir(parents=True)
        bundle_path.write_bytes(b"current attested bytes")
        if case.extra_bundle_attempt is not None:
            extra_path = bundle_path.with_name(
                f"cmd-mox-{TAG}-run73-{case.extra_bundle_attempt}.sigstore.json"
            )
            extra_path.write_bytes(b"another attested bundle")
    return bundle_name


def _run_bundle_attachment(
    tmp_path: Path,
    case: _BundleAttachmentCase,
) -> tuple[
    subprocess.CompletedProcess[str],
    dict[str, str],
    list[dict[str, object]],
    str,
]:
    """Run the checked-in draft-bundle step with one release inventory.

    Returns
    -------
    tuple
        Shell result, declared environment, recorded GitHub calls, and bundle
        filename.
    """
    bundle_name = _write_bundle_inventory(tmp_path, case)
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
    script, declared_env = load_workflow_step("draft-release", "attach-bundle")
    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_TOOL_LOG": str(log),
            "GH_ASSETS_JSON": json.dumps({
                "assets": [
                    {"name": bundle_name}
                    if case.bundle_state == "present"
                    else {"name": WHEEL}
                ],
                "isDraft": case.release_state == "draft",
            }),
            "GH_RELEASE_EXISTS": "1",
            "PATH": f"{tools}:/usr/bin:/bin",
            "RUN_ID": "73",
            "TAG_NAME": TAG,
        },
    )
    calls = read_tool_calls(log, tool="gh")
    return result, declared_env, calls, bundle_name


@pytest.mark.parametrize(
    ("release_state", "bundle_state"),
    [("draft", "missing"), ("draft", "present"), ("published", "missing")],
    ids=["draft-missing-bundle", "draft-existing-bundle", "published-release"],
)
def test_draft_step_attaches_current_bundle_only_when_missing(
    tmp_path: Path, release_state: str, bundle_state: str
) -> None:
    """A draft retains this run's bundle without overwriting existing assets."""
    result, declared_env, calls, bundle_name = _run_bundle_attachment(
        tmp_path, _BundleAttachmentCase(release_state, bundle_state)
    )
    expected_calls = [gh_call(["release", "view", TAG, "--json", "isDraft,assets"])]
    if release_state == "draft" and bundle_state == "missing":
        expected_calls.append(
            gh_call(["release", "upload", TAG, f"dist/attestations/{bundle_name}"])
        )
    assert result.returncode == 0, result.stderr
    assert declared_env == {
        "RUN_ID": "${{ github.run_id }}",
    }, "the attachment step must receive its run identity through env"
    assert calls == expected_calls, (
        "only a missing bundle on a draft release may be uploaded"
    )
    commands = [typ.cast("list[str]", call["argv"]) for call in calls]
    assert all("--clobber" not in argv for argv in commands), (
        "the draft bundle upload must never overwrite a release asset"
    )
    step_ids = [
        step.get("id") for step in load_workflow()["jobs"]["draft-release"]["steps"]
    ]
    assert step_ids.index("attach-bundle") < step_ids.index("snapshot"), (
        "the carry-over snapshot must include the attached current-run bundle"
    )


def test_draft_rerun_reuses_bundle_from_earlier_attempt(tmp_path: Path) -> None:
    """A failed-jobs rerun reuses the attested bundle from attempt one."""
    result, _, calls, bundle_name = _run_bundle_attachment(
        tmp_path,
        _BundleAttachmentCase("draft", "missing", bundle_attempt="1"),
    )

    assert result.returncode == 0, result.stderr
    assert calls[-1] == gh_call([
        "release",
        "upload",
        TAG,
        f"dist/attestations/{bundle_name}",
    ]), "the rerun must attach the bundle carried by release-dist"


def test_draft_step_fails_when_multiple_current_run_bundles_exist(
    tmp_path: Path,
) -> None:
    """Ambiguous current-run bundle inventory must stop before GitHub calls."""
    result, _, calls, _ = _run_bundle_attachment(
        tmp_path,
        _BundleAttachmentCase("draft", "missing", extra_bundle_attempt="1"),
    )

    assert result.returncode == 1, "multiple matching bundles must fail safely"
    assert "expected exactly one current-run provenance bundle" in result.stdout, (
        "the workflow must explain its ambiguous bundle inventory"
    )
    assert calls == [], "an ambiguous bundle inventory must stop before GitHub calls"


def test_draft_step_fails_when_current_bundle_is_missing(tmp_path: Path) -> None:
    """A release cannot continue without the bundle attested by this run."""
    result, _, calls, _ = _run_bundle_attachment(
        tmp_path,
        _BundleAttachmentCase("draft", "missing", bundle_attempt=None),
    )

    assert result.returncode == 1, "a missing bundle must stop draft publication"
    assert "expected exactly one current-run provenance bundle" in result.stdout, (
        "the workflow must explain why the draft step failed"
    )
    assert calls == [], "the workflow must not call GitHub without its bundle"
