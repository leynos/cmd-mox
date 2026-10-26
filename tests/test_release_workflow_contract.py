"""Contract checks for the release workflow's permissions and job graph.

Run with ``make test`` or
``python -m pytest -q tests/test_release_workflow_contract.py``. These checks
parse only the release and CI workflows plus the local distribution action.
"""

from __future__ import annotations

import re
import typing as typ
from pathlib import Path

import pytest
import yaml
from helpers.release_workflow import (
    RELEASE_WORKFLOW_PATH,
    Workflow,
    WorkflowJob,
    WorkflowStep,
    should_skip_release_tests_for_mutation_sandbox,
    workflow_triggers,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_ASSETS_CLI = _REPOSITORY_ROOT / "scripts" / "release_assets_cli.py"
_CI_WORKFLOW = _REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
_BUILD_ACTION = (
    _REPOSITORY_ROOT / ".github" / "actions" / "pure-python-wheel" / "action.yml"
)
pytestmark = pytest.mark.skipif(
    should_skip_release_tests_for_mutation_sandbox(
        Path.cwd(), RELEASE_WORKFLOW_PATH, _ASSETS_CLI
    ),
    reason="mutmut sandbox omits the release workflow and CLI",
)

_EXPECTED_PERMISSIONS = {
    "check-version": {"contents": "read"},
    "build-dist": {"contents": "read"},
    "attest": {"contents": "read", "id-token": "write", "attestations": "write"},
    "draft-release": {"contents": "write"},
    "publish-pypi": {"contents": "read", "id-token": "write"},
    "publish-release": {"contents": "write"},
}
_EXPECTED_NEEDS = {
    "check-version": [],
    "build-dist": [],
    "attest": ["check-version", "build-dist"],
    "draft-release": ["check-version", "attest"],
    "publish-pypi": ["attest", "draft-release"],
    "publish-release": ["check-version", "draft-release", "publish-pypi"],
}
_SHA_PIN = re.compile(r"^[0-9a-f]{40}$")


def _workflow(path: Path) -> Workflow:
    """Load one workflow document with PyYAML's safe loader.

    Returns
    -------
    Workflow
        Parsed workflow document.
    """
    return typ.cast("Workflow", yaml.safe_load(path.read_text(encoding="utf-8")))


def _jobs(workflow: Workflow) -> dict[str, WorkflowJob]:
    """Return the jobs mapping with YAML values narrowed for assertions.

    Returns
    -------
    dict[str, WorkflowJob]
        Workflow jobs indexed by job ID.
    """
    return workflow["jobs"]


def _needs(job: WorkflowJob) -> list[str]:
    """Normalize a job's optional ``needs`` entry to a list.

    Returns
    -------
    list[str]
        Dependency job IDs, or an empty list.
    """
    needs = job.get("needs", [])
    return [needs] if isinstance(needs, str) else list(needs)


def _steps(job: WorkflowJob) -> list[WorkflowStep]:
    """Return a job's steps as a list of mappings.

    Returns
    -------
    list[WorkflowStep]
        Step mappings from the parsed workflow.
    """
    return job["steps"]


def _run_scripts(workflow_path: Path, job_ids: set[str]) -> list[str]:
    """Collect ``run`` blocks from only the named workflow jobs.

    Returns
    -------
    list[str]
        Shell scripts declared by those jobs.
    """
    jobs = _jobs(_workflow(workflow_path))
    return [
        run
        for job_id in job_ids
        for step in _steps(jobs[job_id])
        if isinstance((run := step.get("run")), str)
    ]


def _action_run_scripts(action_path: Path) -> list[str]:
    """Collect shell scripts from a composite action's ``run`` steps.

    Returns
    -------
    list[str]
        Shell scripts declared by the composite action.
    """
    action = yaml.safe_load(action_path.read_text(encoding="utf-8"))
    return [step["run"] for step in action["runs"]["steps"] if "run" in step]


def test_release_trigger_concurrency_and_top_level_permissions() -> None:
    """Tags use the requested pattern and release runs queue rather than cancel."""
    workflow = _workflow(RELEASE_WORKFLOW_PATH)
    triggers = workflow_triggers(workflow)

    assert triggers["push"]["tags"] == ["v*.*.*"], (
        "release workflow must run for version tags"
    )
    assert workflow["permissions"] == {}, "top-level permissions must be empty"
    assert workflow["concurrency"] == {
        "group": "release-${{ github.ref }}",
        "cancel-in-progress": False,
    }, "a repeated tag must queue behind the active upload"


def test_release_tests_skip_missing_files_only_in_mutation_sandbox(
    tmp_path: Path,
) -> None:
    """Only mutmut's intentionally sparse checkout may omit release files."""
    workflow = tmp_path / ".github" / "workflows" / "release.yml"
    cli = tmp_path / "scripts" / "release_assets_cli.py"
    mutation_root = tmp_path / "mutants"
    checkout_root = tmp_path / "checkout"

    assert should_skip_release_tests_for_mutation_sandbox(
        mutation_root, workflow, cli
    ), "release tests must skip when mutmut omits workflow and CLI files"
    assert not should_skip_release_tests_for_mutation_sandbox(
        checkout_root, workflow, cli
    ), "a normal checkout must fail rather than skip when release files are missing"

    workflow.parent.mkdir(parents=True)
    cli.parent.mkdir(parents=True)
    workflow.touch()
    cli.touch()
    assert not should_skip_release_tests_for_mutation_sandbox(
        mutation_root, workflow, cli
    ), "present release files must keep mutation-sandbox checks active"


def test_release_jobs_have_exact_permissions_and_dependencies() -> None:
    """Each release phase gets only its required scope and dependency edges."""
    workflow = _workflow(RELEASE_WORKFLOW_PATH)
    jobs = _jobs(workflow)

    assert set(jobs) == set(_EXPECTED_PERMISSIONS), (
        "the release workflow job graph must contain exactly the six phases"
    )
    for job_id, expected in _EXPECTED_PERMISSIONS.items():
        assert jobs[job_id]["permissions"] == expected, (
            f"{job_id} permissions must be least-privilege"
        )
        assert _needs(jobs[job_id]) == _EXPECTED_NEEDS[job_id], (
            f"{job_id} has an unexpected dependency graph"
        )

    assert jobs["publish-pypi"]["environment"] == {
        "name": "pypi",
        "url": "https://pypi.org/project/cmd-mox/",
    }, "PyPI publishing must use the configured trusted-publishing environment"
    check_version_dependants = {
        "attest",
        "draft-release",
        "publish-pypi",
        "publish-release",
    }
    assert check_version_dependants <= set(jobs), "all publisher jobs must exist"
    assert jobs["attest"]["needs"].count("check-version") == 1, (
        "attestation must wait directly for version validation"
    )
    assert "attest" in jobs["publish-pypi"]["needs"], (
        "PyPI must wait for validated, attested distributions"
    )


def test_every_action_is_local_or_pinned_and_checkout_credentials_are_disabled() -> (
    None
):
    """Release actions use local paths or immutable SHA pins."""
    workflow = _workflow(RELEASE_WORKFLOW_PATH)
    action = yaml.safe_load(_BUILD_ACTION.read_text(encoding="utf-8"))
    uses_values = [
        step["uses"]
        for job in _jobs(workflow).values()
        for step in _steps(job)
        if "uses" in step
    ] + [step["uses"] for step in action["runs"]["steps"] if "uses" in step]

    for uses in uses_values:
        assert uses.startswith("./") or _SHA_PIN.fullmatch(uses.rpartition("@")[2]), (
            f"action must be local or pinned to a lowercase full SHA: {uses}"
        )
    checkouts = [uses for uses in uses_values if uses.startswith("actions/checkout@")]
    assert checkouts, "release workflow must check out scripts"
    for job in _jobs(workflow).values():
        for step in _steps(job):
            if step.get("uses", "").startswith("actions/checkout@"):
                assert step.get("with", {}).get("persist-credentials") is False, (
                    "every release checkout must disable persisted credentials"
                )


def test_release_workflow_attests_and_uploads_without_clobbering() -> None:
    """One release-dist artefact carries provenance and PyPI skips existing files."""
    workflow_text = RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = _workflow(RELEASE_WORKFLOW_PATH)
    jobs = _jobs(workflow)
    all_uses = [
        step["uses"] for job in jobs.values() for step in _steps(job) if "uses" in step
    ]

    assert any(
        uses.startswith("actions/attest-build-provenance@") for uses in all_uses
    ), "the workflow must create a build provenance attestation"
    assert "name: release-dist" in workflow_text, (
        "attested files and their bundle must share release-dist"
    )
    assert any(
        step.get("uses", "").startswith("actions/download-artifact@")
        and step.get("with", {}).get("name") == "release-dist"
        for job in jobs.values()
        for step in _steps(job)
    ), "later phases must download the attested release-dist artefact"
    pypi_publish = next(
        step
        for step in _steps(jobs["publish-pypi"])
        if step.get("uses", "").startswith("pypa/gh-action-pypi-publish@")
    )
    assert pypi_publish["with"]["skip-existing"] is True, (
        "PyPI uploads must tolerate an earlier partial upload"
    )
    assert "--clobber" not in workflow_text, "existing GitHub assets are immutable"


def test_run_scripts_are_quoted_shell_steps_without_workflow_interpolation() -> None:
    """Release shell uses env variables, and each script enables strict Bash."""
    scripts = _run_scripts(
        RELEASE_WORKFLOW_PATH,
        set(_EXPECTED_NEEDS),
    )
    ci_scripts = _run_scripts(_CI_WORKFLOW, {"build-dist"})
    action_scripts = _action_run_scripts(_BUILD_ACTION)
    fetch_script = (_REPOSITORY_ROOT / "scripts" / "fetch_pypi_index.sh").read_text(
        encoding="utf-8"
    )

    for script in [*scripts, *ci_scripts, *action_scripts, fetch_script]:
        first_line = script.splitlines()[0].strip()
        assert first_line == "set -euo pipefail", (
            "every checked-in release shell step must start in strict mode"
        )
        assert "${{" not in script, (
            "workflow expressions must enter scripts only through env:"
        )


def test_ci_builds_distributions_on_pull_requests_and_native_matrix_is_removed() -> (
    None
):
    """Pull requests prove the shared action creates both distributions."""
    ci = _workflow(_CI_WORKFLOW)
    triggers = workflow_triggers(ci)
    jobs = _jobs(ci)

    assert "pull_request" in triggers, "CI must build distributions on pull requests"
    assert jobs["build-dist"]["permissions"] == {"contents": "read"}, (
        "distribution CI needs only repository read access"
    )
    build_steps = _steps(jobs["build-dist"])
    assert any(
        step.get("uses") == "./.github/actions/pure-python-wheel"
        and step.get("with", {}).get("artifact-name") == "wheels-pure"
        for step in build_steps
    ), "build-dist must use the shared distribution action"
    assert not (
        _REPOSITORY_ROOT / ".github" / "workflows" / "build-wheels.yml"
    ).exists(), "the unused native wheel matrix workflow must be removed"
    assert not (_REPOSITORY_ROOT / ".github" / "actions" / "build-wheels").exists(), (
        "the unused native wheel action must be removed"
    )
