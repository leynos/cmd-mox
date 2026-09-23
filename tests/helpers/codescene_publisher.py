"""What the single CodeScene publisher must look like, as readings.

CV-005 gives the upload to one workflow that runs on pushes to main and
serves no pull request. Its upload step is guarded on the ref as well as
the token, binds the token itself and nowhere else, and its runs queue
rather than cancel. Every reading is pure over supplied documents and
returns the violations it finds, so a contract can drive it over a
constructed workflow as readily as over this repository's own.
"""

from __future__ import annotations

import re
import typing as typ

from tests.helpers.codescene_binding import CHECK_STEP_ID
from tests.helpers.codescene_reach import (
    COVERAGE_ACTION,
    UPLOAD_ACTION,
    normalized,
    upload_references,
)
from tests.helpers.workflow_reading import (
    WORKFLOW_DIRECTORY,
    WorkflowReadingError,
    all_steps,
    filter_names,
    jobs,
    steps,
    triggers,
)

if typ.TYPE_CHECKING:
    from tests.helpers.workflow_reading import Document

#: The conjuncts the upload step's ``if:`` must consist of, exactly.
#: Exact rather than "contains": an extra conjunct such as ``false``
#: disables the upload with nothing failing, and an ``||`` anywhere lands
#: inside some conjunct and so fails the comparison too.
UPLOAD_GUARD: typ.Final[frozenset[str]] = frozenset({
    f"steps.{CHECK_STEP_ID}.outputs.available == 'true'",
    "github.ref == 'refs/heads/main'",
})

#: The triggers a publisher may declare. A dispatch can aim at any
#: branch, which is why the ref guard is required on the step.
PUBLISHER_TRIGGERS: typ.Final[frozenset[str]] = frozenset({
    "push",
    "workflow_dispatch",
})

#: A full-length commit pin.
PINNED_COMMIT: typ.Final[re.Pattern[str]] = re.compile(r"@[0-9a-f]{40}$")

#: The publisher's concurrency group, exactly: keyed on the ref and
#: nothing else. With one group per ref, runs never overlap and the
#: survivor of any replacement is the newest trigger, whose commit is the
#: newest main at trigger time, so triggered runs (push and dispatch)
#: upload in commit order. A manual re-run of an older main run keeps its
#: old commit: an operator action that republishes that commit's coverage
#: and baseline until the next push supersedes it. Keying on
#: the event as well would let an earlier dispatch finish after a newer
#: push and upload older coverage last; a constant group would let a
#: branch dispatch replace main's pending run and then skip the upload.
PUBLISHER_GROUP: typ.Final[str] = "coverage-main-${{ github.ref }}"


def conjuncts(condition: object) -> frozenset[str]:
    """Return an ``if:`` condition's ``&&`` terms, whitespace-normalized.

    Returns
    -------
    frozenset[str]
        The terms, with any ``${{ }}`` wrapper removed.

    Examples
    --------
    >>> sorted(conjuncts("${{ a == 'b' &&  c }}"))
    ["a == 'b'", 'c']
    """
    body = normalized(condition)
    if body.startswith("${{") and body.endswith("}}"):
        body = body[3:-2]
    return frozenset(normalized(term) for term in body.split("&&"))


def uploaders(documents: dict[str, Document]) -> list[str]:
    """Return every document that invokes the CodeScene upload action.

    Returns
    -------
    list[str]
        Their keys.
    """
    return [name for name, document in documents.items() if upload_references(document)]


def publisher(documents: dict[str, Document]) -> tuple[str, Document]:
    """Return the one publisher: the only document invoking the upload.

    Found rather than named, so a second uploader cannot hide beside it.

    Returns
    -------
    tuple[str, Document]
        Its key and document.

    Raises
    ------
    WorkflowReadingError
        If the upload is invoked by no document, by several, or by a
        document that is not a workflow.
    """
    found = uploaders(documents)
    if len(found) != 1 or not found[0].startswith(WORKFLOW_DIRECTORY):
        msg = f"exactly one workflow must invoke {UPLOAD_ACTION}; found {found}"
        raise WorkflowReadingError(msg)
    return found[0], documents[found[0]]


def upload_step(document: Document) -> dict[object, object]:
    """Return a publisher's single upload step.

    Returns
    -------
    dict[object, object]
        The step.

    Raises
    ------
    WorkflowReadingError
        If the action is invoked other than by exactly one step.
    """
    found = [
        step
        for step in all_steps(document)
        if UPLOAD_ACTION.casefold() in str(step.get("uses", "")).casefold()
    ]
    if len(found) != 1:
        msg = f"the publisher must invoke the upload once; it does {len(found)} times"
        raise WorkflowReadingError(msg)
    return found[0]


def trigger_violations(document: Document) -> list[str]:
    """Return why a publisher's triggers could publish anything but main.

    Returns
    -------
    list[str]
        One entry per violation.
    """
    declared = triggers(document)
    found = [f"trigger {name}" for name in declared if name not in PUBLISHER_TRIGGERS]
    push = declared.get("push")
    if not isinstance(push, dict) or set(push) - {"branches", "paths", "paths-ignore"}:
        found.append(f"push filter {push!r} is not a branches filter")
    elif filter_names(push.get("branches")) != ["main"]:
        found.append(f"push branches {push.get('branches')!r} are not [main]")
    return found


def guard_violations(step: dict[object, object]) -> list[str]:
    """Return why an upload step's ``if:`` is not exactly the required guard.

    Returns
    -------
    list[str]
        One entry when the conjuncts differ from ``UPLOAD_GUARD``.
    """
    found = conjuncts(step.get("if", ""))
    if found == UPLOAD_GUARD:
        return []
    return [f"guard {sorted(found)} is not {sorted(UPLOAD_GUARD)}"]


def input_violations(step: dict[object, object]) -> list[str]:
    """Return why an upload step does not upload, pinned.

    Returns
    -------
    list[str]
        One entry per violation.
    """
    inputs = step.get("with")
    mode = inputs.get("mode") if isinstance(inputs, dict) else None
    found = [] if mode == "upload" else [f"mode is {mode!r}, not 'upload'"]
    if not PINNED_COMMIT.search(str(step.get("uses", ""))):
        found.append(f"{step.get('uses')!r} is not pinned to a commit")
    return found


def concurrency_violations(document: Document) -> list[str]:
    """Return why a publisher's runs could cancel or displace one another.

    A concurrency group without ``cancel-in-progress`` keeps one pending
    run per group: a newer push replaces an older pending run and never
    cancels a running one, so the newest baseline wins. A cancelled run
    abandons both its upload and its baseline write. The workflow's group
    must be exactly ``PUBLISHER_GROUP``, and no job may declare a group of
    its own, since a second group would let runs overlap.

    Returns
    -------
    list[str]
        One entry per violation.
    """
    declared = document.get("concurrency")
    if not isinstance(declared, dict) or not declared.get("group"):
        return [f"no workflow-level concurrency group: {declared!r}"]
    job_scopes = (job.get("concurrency") for job in jobs(document).values())
    scopes = [scope for scope in [declared, *job_scopes] if isinstance(scope, dict)]
    return _group_violations(document, declared) + _cancelling_scopes(scopes)


def _group_violations(document: Document, declared: dict[object, object]) -> list[str]:
    """Return why the publisher's runs do not share exactly one group per ref.

    Returns
    -------
    list[str]
        One entry for a wrong workflow group and one per job-level group.
    """
    group = normalized(declared.get("group"))
    found = (
        []
        if group == PUBLISHER_GROUP
        else [f"concurrency group {group!r} is not exactly {PUBLISHER_GROUP!r}"]
    )
    return found + [
        f"job {name} declares its own concurrency"
        for name, job in jobs(document).items()
        if "concurrency" in job
    ]


def _cancelling_scopes(scopes: list[dict[object, object]]) -> list[str]:
    """Return the concurrency scopes that cancel a run in progress.

    Returns
    -------
    list[str]
        One entry per cancelling scope.
    """
    return [
        f"cancel-in-progress {scope.get('cancel-in-progress')!r}"
        for scope in scopes
        if normalized(scope.get("cancel-in-progress", "false")) != "false"
    ]


def neutralized_steps(document: Document) -> list[str]:
    """Return the publisher's jobs or coverage steps that a condition can skip.

    The upload's own guard is held exactly by ``guard_violations``; the
    job around it and the step writing the report must run whenever the
    workflow does, or an ``if: false`` stops publishing with nothing red.

    Returns
    -------
    list[str]
        One entry per conditional job or coverage step.
    """
    found = [
        f"job {name} has if:" for name, job in jobs(document).items() if "if" in job
    ]
    found += [
        f"coverage step {step.get('name')!r} has if:"
        for step in all_steps(document)
        if COVERAGE_ACTION in str(step.get("uses", "")) and "if" in step
    ]
    return found


def _swallows_failure(item: dict[object, object]) -> bool:
    """Return whether a job or step declares a ``continue-on-error`` other than false.

    Returns
    -------
    bool
        True when a failure there would not fail the run.
    """
    return normalized(item.get("continue-on-error", "false")) != "false"


def swallowed_failures(document: Document) -> list[str]:
    """Return the coverage or upload work whose failure cannot fail the run.

    ``continue-on-error`` silences a ratchet failure or a failed upload as
    surely as ``if: false`` skips it, so it is refused on every coverage
    and upload step and on every job holding one.

    Returns
    -------
    list[str]
        One entry per job or step that swallows its failure.
    """
    found: list[str] = []
    for name, job in jobs(document).items():
        watched = [
            step
            for step in steps(job)
            if COVERAGE_ACTION in str(step.get("uses", ""))
            or UPLOAD_ACTION in str(step.get("uses", ""))
        ]
        if watched and _swallows_failure(job):
            found.append(f"job {name} has continue-on-error")
        found += [
            f"step {step.get('name')!r} has continue-on-error"
            for step in watched
            if _swallows_failure(step)
        ]
    return found
