"""Tests for draft creation and release-state snapshot behaviour."""

from __future__ import annotations

import json
import typing as typ

import pytest
from helpers.release_github import (
    TAG,
    WHEEL,
    gh_call,
    prepare_snapshot_files,
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


@pytest.mark.parametrize("missing_bundle", [False, True])
def test_snapshot_reuses_seeded_assets_and_downloads_missing_bundle(
    tmp_path: Path, *, missing_bundle: bool
) -> None:
    """The snapshot reuses seeded files and downloads only a missing bundle."""
    downloads, prior_bundle = prepare_snapshot_files(
        tmp_path, missing_bundle=missing_bundle
    )
    tools = install_fake_tools(tmp_path / "bin")
    log = tmp_path / "tool-calls.jsonl"
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
    assert [
        call
        for call in calls
        if typ.cast("list[str]", call["argv"])[:2] == ["release", "download"]
    ] == (
        [
            gh_call([
                "release",
                "download",
                TAG,
                "--pattern",
                prior_bundle,
                "--dir",
                "github-state/files",
            ])
        ]
        if missing_bundle
        else []
    ), "the snapshot must reuse cached files and fetch only the missing bundle"


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
