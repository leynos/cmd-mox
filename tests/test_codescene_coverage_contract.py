"""CV-005: CodeScene coverage belongs to main, and to main alone.

One workflow uploads coverage to CodeScene: ``coverage-main.yml``, on
pushes to main. Nothing a pull request can run, directly or through a
local reusable workflow or action, names the upload action, runs
``cs-coverage``, reaches ``CS_ACCESS_TOKEN``, or names the CodeScene host.
The pull-request lane keeps ``generate-coverage`` with the ratchet,
against the baseline the publisher writes, and publishes nothing.

The rule is a policy rather than a gap. A fork cannot read the token, so
a changed-line check on the pull-request lane was a silent skip for the
contributions least likely to have been measured, and when the CodeScene
project stopped returning a gates configuration that check failed every
pull request over a defect in none of them. What CV-005 unpins is the
call, not the artefact: the uploader pins the CLI through its own
manifest, but the CLI talks to an API whose answers have changed shape.

These tests read this repository's files. The constructed cases proving
that each clause catches what it names are in
``test_codescene_closure_cases.py`` and
``test_codescene_publisher_cases.py``.
"""

from __future__ import annotations

import typing as typ
from pathlib import Path

import pytest

from tests.helpers.codescene_publisher import (
    binding_violations,
    concurrency_violations,
    conjuncts,
    guard_violations,
    input_violations,
    neutralized_steps,
    publisher,
    trigger_violations,
    upload_step,
)
from tests.helpers.codescene_reach import (
    COVERAGE_ACTION,
    pull_request_breaches,
    retired_names,
)
from tests.helpers.workflow_closure import pull_request_surface, refused_references
from tests.helpers.workflow_reading import all_steps, read_workflow_tree, triggers

if typ.TYPE_CHECKING:
    from tests.helpers.workflow_reading import Document

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "leynos/cmd-mox"

pytestmark = pytest.mark.skipif(
    not (REPOSITORY_ROOT / ".github" / "workflows").is_dir(),
    reason=(
        "workflows not present in this working copy (for example inside "
        "mutmut's mutants/ sandbox, which does not copy .github/)"
    ),
)

#: The pull-request coverage step's guard, exactly: Linux only, and only
#: on a pull request, since pushes to main measure in the publisher.
PULL_REQUEST_COVERAGE_GUARD: typ.Final[frozenset[str]] = frozenset({
    "matrix.run-windows-smoke == false",
    "github.event_name == 'pull_request'",
})

#: The publisher's triggers, exactly. Dropping one is as silent as adding
#: one: a publisher that loses its push trigger never publishes again.
PUBLISHER_TRIGGERS: typ.Final[frozenset[str]] = frozenset({"push"})

#: What both coverage lanes measure, exactly. The lanes are held equal to
#: each other below; this pins the shared selection itself, so a change
#: made to both at once is still a reviewed change here.
COVERAGE_SELECTION: typ.Final[dict[object, object]] = {
    "output-path": "coverage.xml",
    "format": "cobertura",
    "pytest-workers": "",
    "with-ratchet": "true",
}

#: Inputs that change what happens to a report rather than what it
#: measures; the lanes may differ on these and nothing else.
NON_SELECTION_INPUTS: typ.Final[frozenset[str]] = frozenset({
    "publish-artefact",
    "artefact-name-suffix",
})


@pytest.fixture(scope="module")
def documents() -> dict[str, Document]:
    """Return every workflow and local action, parsed once.

    Returns
    -------
    dict[str, Document]
        Repository-relative path to document.
    """
    return read_workflow_tree(REPOSITORY_ROOT)


def _coverage_steps(document: Document) -> list[dict[object, object]]:
    """Return a document's generate-coverage steps.

    Returns
    -------
    list[dict[object, object]]
        The steps.
    """
    return [
        step for step in all_steps(document) if COVERAGE_ACTION in str(step.get("uses"))
    ]


def _inputs(step: dict[object, object]) -> dict[object, object]:
    """Return a step's ``with:`` mapping, which must be one.

    Returns
    -------
    dict[object, object]
        The inputs.
    """
    inputs = step.get("with")
    assert isinstance(inputs, dict), f"step {step.get('name')!r} has no with: mapping"
    return inputs


def _selection(step: dict[object, object]) -> dict[object, object]:
    """Return the inputs that decide what a coverage step measures.

    Returns
    -------
    dict[object, object]
        The inputs other than ``NON_SELECTION_INPUTS``.
    """
    return {
        key: value
        for key, value in _inputs(step).items()
        if key not in NON_SELECTION_INPUTS
    }


def test_the_pull_request_surface_never_reaches_codescene(
    documents: dict[str, Document],
) -> None:
    """No road to CodeScene from anything a pull request runs."""
    surface = pull_request_surface(documents, REPOSITORY)
    breaches = pull_request_breaches(surface)
    assert not breaches, f"pull-request lanes reach CodeScene: {breaches}"


def test_every_reference_on_the_surface_can_be_followed(
    documents: dict[str, Document],
) -> None:
    """A qualified self-call runs code this checkout cannot show."""
    surface = pull_request_surface(documents, REPOSITORY)
    refused = refused_references(surface, REPOSITORY)
    assert not refused, f"references the closure cannot follow: {refused}"


def test_the_publisher_serves_main_alone(documents: dict[str, Document]) -> None:
    """Exactly one workflow uploads; it is off the surface and pushes main."""
    name, document = publisher(documents)
    assert name == ".github/workflows/coverage-main.yml", f"publisher is {name}"
    surface = pull_request_surface(documents, REPOSITORY)
    assert name not in surface, f"{name} is reachable from a pull request"
    violations = trigger_violations(document)
    assert not violations, f"{name} can publish more than main: {violations}"
    declared = frozenset(triggers(document))
    assert declared == PUBLISHER_TRIGGERS, (
        f"{name} declares {sorted(declared)}, not {sorted(PUBLISHER_TRIGGERS)}"
    )


def test_the_upload_is_guarded_on_the_ref_and_the_token(
    documents: dict[str, Document],
) -> None:
    """The guard is exactly the token and the main ref."""
    _, document = publisher(documents)
    violations = guard_violations(upload_step(document))
    assert not violations, f"upload guard: {violations}"


def test_the_token_is_bound_on_the_upload_step_alone(
    documents: dict[str, Document],
) -> None:
    """The step binds the secret and passes it; no other scope holds it."""
    _, document = publisher(documents)
    violations = binding_violations(document, upload_step(document))
    assert not violations, f"token binding: {violations}"


def test_the_upload_uploads_from_a_pinned_action(
    documents: dict[str, Document],
) -> None:
    """``mode: upload`` is named, and the action is pinned to a commit."""
    _, document = publisher(documents)
    violations = input_violations(upload_step(document))
    assert not violations, f"upload inputs: {violations}"


def test_publisher_runs_queue_rather_than_cancel(
    documents: dict[str, Document],
) -> None:
    """A cancelled run abandons its upload and its baseline write."""
    _, document = publisher(documents)
    violations = concurrency_violations(document)
    assert not violations, f"publisher concurrency: {violations}"


def test_nothing_in_the_publisher_can_skip_the_measurement(
    documents: dict[str, Document],
) -> None:
    """Only the upload step carries a condition, and it is held exactly."""
    _, document = publisher(documents)
    violations = neutralized_steps(document)
    assert not violations, f"conditional publisher work: {violations}"


def test_no_document_carries_the_retired_checksum(
    documents: dict[str, Document],
) -> None:
    """The checksum variable, its refresher, and the input it fed are gone."""
    found = [
        f"{name}: {site}"
        for name, document in documents.items()
        for site in retired_names(document)
    ]
    assert not found, f"retired checksum plumbing remains: {found}"
    assert ".github/workflows/get-codescene-sha.yml" not in documents, (
        "the checksum refresher workflow must be deleted"
    )


def test_the_pull_request_lane_ratchets_and_publishes_nothing(
    documents: dict[str, Document],
) -> None:
    """The ratchet is the gate CV-005 leaves, so it must stay switched on.

    Deleting ``with-ratchet`` leaves every refusal above green while the
    lane stops gating anything; a condition such as ``false && ...``
    does the same, so the step's guard is held exactly.
    """
    surface = pull_request_surface(documents, REPOSITORY)
    lane = [step for document in surface.values() for step in _coverage_steps(document)]
    assert len(lane) == 1, f"expected one pull-request coverage step, found {lane}"
    (step,) = lane
    inputs = _inputs(step)
    assert inputs.get("with-ratchet") == "true", "the pull-request lane must ratchet"
    assert inputs.get("publish-artefact") == "false", (
        "the pull-request lane must not publish a coverage artefact"
    )
    guard = conjuncts(step.get("if", ""))
    assert guard == PULL_REQUEST_COVERAGE_GUARD, f"coverage guard is {sorted(guard)}"


def test_the_pull_request_lane_measures_what_the_baseline_measures(
    documents: dict[str, Document],
) -> None:
    """A ratchet against a differently built baseline measures the builds."""
    _, document = publisher(documents)
    baseline = _coverage_steps(document)
    assert len(baseline) == 1, f"the publisher runs {len(baseline)} coverage steps"
    assert _inputs(baseline[0]).get("with-ratchet") == "true", (
        "the publisher must write the ratchet baseline"
    )
    assert _selection(baseline[0]) == COVERAGE_SELECTION, (
        f"the baseline measures {_selection(baseline[0])}, not {COVERAGE_SELECTION}"
    )
    surface = pull_request_surface(documents, REPOSITORY)
    for lane_document in surface.values():
        for step in _coverage_steps(lane_document):
            assert _selection(step) == _selection(baseline[0]), (
                f"{_selection(step)} is built differently from the baseline's "
                f"{_selection(baseline[0])}"
            )
            assert step.get("uses") == baseline[0].get("uses"), (
                "the lane and the baseline must run one generator commit"
            )
