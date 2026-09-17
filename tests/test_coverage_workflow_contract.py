"""Contract-test main-owned CodeScene coverage publication workflows."""

from __future__ import annotations

from pathlib import Path

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
MAIN_COVERAGE_WORKFLOW_PATH = (
    REPOSITORY_ROOT / ".github" / "workflows" / "coverage-main.yml"
)
GENERATE_COVERAGE_ACTION = "leynos/shared-actions/.github/actions/generate-coverage"
UPLOAD_CODESCENE_ACTION = (
    "leynos/shared-actions/.github/actions/upload-codescene-coverage"
)


def _load_workflow(path: Path) -> dict[str, object]:
    """Return one GitHub Actions workflow with PyYAML's boolean-key quirk fixed.

    Returns
    -------
    dict[str, object]
        The decoded workflow document.
    """
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), f"{path} must contain a mapping"
    if True in workflow:
        workflow["on"] = workflow.pop(True)
    return workflow


def _job(workflow: dict[str, object], name: str) -> dict[str, object]:
    """Return a named job after asserting the workflow's job mapping shape.

    Returns
    -------
    dict[str, object]
        The named job declaration.
    """
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "workflow must declare a jobs mapping"
    job = jobs.get(name)
    assert isinstance(job, dict), f"workflow must declare the {name!r} job"
    return job


def _step(job: dict[str, object], name: str) -> dict[str, object]:
    """Return a named step after asserting that the job has a steps list.

    Returns
    -------
    dict[str, object]
        The named step declaration.
    """
    steps = job.get("steps")
    assert isinstance(steps, list), "job must declare a steps list"
    step = next(
        (
            candidate
            for candidate in steps
            if isinstance(candidate, dict) and candidate.get("name") == name
        ),
        None,
    )
    assert isinstance(step, dict), f"job must declare a {name!r} step"
    return step


def _uses(step: dict[str, object]) -> str:
    """Return a step action reference after validating its scalar shape.

    Returns
    -------
    str
        The action reference.
    """
    uses = step.get("uses")
    assert isinstance(uses, str), f"step uses must be a string, got {uses!r}"
    return uses


def _assert_pull_request_trigger_and_environment(
    workflow: dict[str, object],
) -> None:
    """Assert pull-request triggering does not expose CodeScene credentials."""
    triggers = workflow.get("on")
    assert isinstance(triggers, dict), "ci.yml must declare trigger mapping"
    assert "pull_request" in triggers, "ci.yml must run on pull requests"
    workflow_environment = workflow.get("env", {})
    assert isinstance(workflow_environment, dict), "ci.yml env must be a mapping"
    assert "CS_ACCESS_TOKEN" not in workflow_environment, (
        "ci.yml workflow env must not expose CS_ACCESS_TOKEN"
    )


def test_pull_request_coverage_uses_only_the_local_ratchet() -> None:
    """Keep pull-request coverage local and independent of CodeScene credentials."""
    workflow = _load_workflow(CI_WORKFLOW_PATH)
    _assert_pull_request_trigger_and_environment(workflow)

    quality = _job(workflow, "quality")
    job_environment = quality.get("env", {})
    assert isinstance(job_environment, dict), "quality.env must be a mapping"
    assert "CS_ACCESS_TOKEN" not in job_environment, (
        "quality job env must not expose CS_ACCESS_TOKEN"
    )
    assert "CODESCENE_CLI_SHA256" not in job_environment, (
        "quality job env must not expose the CodeScene CLI hash"
    )

    checkout = _step(quality, "Check out repository")
    checkout_inputs = checkout.get("with", {})
    assert isinstance(checkout_inputs, dict), "checkout.with must be a mapping"
    assert checkout_inputs.get("fetch-depth") != 0, (
        "pull-request checkout must not request full history"
    )

    coverage = _step(quality, "Generate coverage")
    assert _uses(coverage).startswith(GENERATE_COVERAGE_ACTION), (
        "pull-request coverage must use the shared generate-coverage action"
    )
    coverage_condition = coverage.get("if")
    assert isinstance(coverage_condition, str), "coverage step must be conditional"
    assert "github.event_name == 'pull_request'" in coverage_condition, (
        "coverage generation must be limited to pull requests"
    )
    coverage_inputs = coverage.get("with")
    assert isinstance(coverage_inputs, dict), "coverage.with must be a mapping"
    assert coverage_inputs.get("with-ratchet") == "true", (
        "pull-request coverage must enable the local ratchet"
    )

    steps = quality.get("steps")
    assert isinstance(steps, list), "quality must declare a steps list"
    assert all(
        UPLOAD_CODESCENE_ACTION not in _uses(step)
        for step in steps
        if isinstance(step, dict) and "uses" in step
    ), "pull-request jobs must not upload coverage to CodeScene"
    assert all(
        "CS_ACCESS_TOKEN" not in step.get("env", {})
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("env", {}), dict)
    ), "pull-request steps must not expose CS_ACCESS_TOKEN"
    assert "codescene.io" not in CI_WORKFLOW_PATH.read_text(encoding="utf-8"), (
        "pull-request workflow must not reference codescene.io"
    )


def test_main_coverage_writes_the_ratchet_and_uploads() -> None:
    """Keep ratchet publication and the CodeScene upload on pushes to main only."""
    workflow = _load_workflow(MAIN_COVERAGE_WORKFLOW_PATH)
    assert workflow.get("on") == {"push": {"branches": ["main"]}}, (
        "coverage-main.yml must run only on pushes to main"
    )

    coverage_upload = _job(workflow, "coverage-upload")
    coverage = _step(coverage_upload, "Generate coverage")
    coverage_inputs = coverage.get("with")
    assert isinstance(coverage_inputs, dict), "coverage.with must be a mapping"
    assert coverage_inputs.get("with-ratchet") == "true", (
        "main coverage must enable the ratchet"
    )

    upload = _step(coverage_upload, "Upload coverage data to CodeScene")
    assert _uses(upload).startswith(UPLOAD_CODESCENE_ACTION), (
        "main coverage must use the shared CodeScene upload action"
    )
    upload_inputs = upload.get("with")
    assert isinstance(upload_inputs, dict), "upload.with must be a mapping"
    assert upload_inputs.get("mode") == "upload", (
        "main coverage upload must use upload mode"
    )
