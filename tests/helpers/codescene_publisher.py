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

from tests.helpers.codescene_reach import (
    COVERAGE_ACTION,
    CREDENTIAL,
    UPLOAD_ACTION,
    upload_references,
)
from tests.helpers.workflow_reading import (
    WORKFLOW_DIRECTORY,
    WorkflowReadingError,
    all_steps,
    filter_names,
    jobs,
    scalars,
    steps,
    triggers,
)

if typ.TYPE_CHECKING:
    from tests.helpers.workflow_reading import Document

#: The id of the step that reports whether the token is available. The
#: token is not bound in the upload step's ``env``: the upload action is
#: composite and hands that ``env`` to its nested upload-artifact and
#: cache steps, while binding the token itself from ``access-token``. So
#: a separate step binds it, runs one exact command, and publishes only
#: whether it is set.
CHECK_STEP_ID: typ.Final[str] = "codescene-token"

#: The check step's command, exactly. Held whole because ``false && X``
#: contains ``X``: a looser match accepts a command that never reports.
CHECK_COMMAND: typ.Final[str] = (
    f'if [ -n "${CREDENTIAL}" ]; then echo "available=true" >> "$GITHUB_OUTPUT"; fi'
)

#: The conjuncts the upload step's ``if:`` must consist of, exactly.
#: Exact rather than "contains": an extra conjunct such as ``false``
#: disables the upload with nothing failing, and an ``||`` anywhere lands
#: inside some conjunct and so fails the comparison too.
UPLOAD_GUARD: typ.Final[frozenset[str]] = frozenset({
    f"steps.{CHECK_STEP_ID}.outputs.available == 'true'",
    "github.ref == 'refs/heads/main'",
})

#: The positive bindings: the check step's ``env`` and the upload's
#: ``access-token``. A guard that reads a binding passes with the binding
#: deleted, because a missing value reads as empty and the upload then
#: skips forever; so each binding itself is required.
STEP_BINDING: typ.Final[str] = f"${{{{ secrets.{CREDENTIAL} }}}}"
ACCESS_TOKEN_INPUT: typ.Final[str] = STEP_BINDING

#: The triggers a publisher may declare. A dispatch can aim at any
#: branch, which is why the ref guard is required on the step.
PUBLISHER_TRIGGERS: typ.Final[frozenset[str]] = frozenset({
    "push",
    "workflow_dispatch",
})

#: A full-length commit pin.
PINNED_COMMIT: typ.Final[re.Pattern[str]] = re.compile(r"@[0-9a-f]{40}$")

#: The expressions every publisher concurrency group must evaluate. The
#: ref keeps a branch dispatch out of main's group; the event keeps a
#: dispatch on main from replacing a pending push, since only a push
#: writes the ratchet baseline. The expressions, not the words: a
#: literal ``coverage-main-github.ref-github.event_name`` names both and
#: evaluates neither, so every run would still share one group.
_GROUP_KEYS: typ.Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"\$\{\{\s*github\.ref\s*\}\}"),
    re.compile(r"\$\{\{\s*github\.event_name\s*\}\}"),
)


def _normalized(text: object) -> str:
    """Return text with runs of whitespace collapsed to single spaces.

    Returns
    -------
    str
        The normalized text.
    """
    return " ".join(str(text).split())


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
    body = _normalized(condition)
    if body.startswith("${{") and body.endswith("}}"):
        body = body[3:-2]
    return frozenset(_normalized(term) for term in body.split("&&"))


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


def check_step(
    document: Document, upload: dict[object, object]
) -> dict[object, object] | None:
    """Return the check step that precedes the upload in its job, if any.

    Returns
    -------
    dict[object, object] or None
        The step with ``CHECK_STEP_ID``, or ``None`` when the upload's job
        runs none before it.
    """
    for job in jobs(document).values():
        job_steps = steps(job)
        positions = [index for index, step in enumerate(job_steps) if step is upload]
        if positions:
            earlier = job_steps[: positions[0]]
            found = [step for step in earlier if step.get("id") == CHECK_STEP_ID]
            return found[0] if found else None
    return None


def _stray_mentions(step: dict[object, object], allowed: set[str]) -> list[str]:
    """Return the paths in one step that name the token outside ``allowed``.

    Returns
    -------
    list[str]
        The paths.
    """
    return [
        scalar.path
        for scalar in scalars(step)
        if CREDENTIAL.casefold() in scalar.text.casefold()
        and scalar.path not in allowed
    ]


def _check_violations(check: dict[object, object] | None) -> list[str]:
    """Return why the check step does not bind the token and report it.

    Returns
    -------
    list[str]
        One entry per violation.
    """
    if check is None:
        return [f"no step with id {CHECK_STEP_ID!r} precedes the upload in its job"]
    env = check.get("env")
    bound = (
        {str(key): _normalized(value) for key, value in env.items()}
        if isinstance(env, dict)
        else {}
    )
    found = (
        []
        if bound == {CREDENTIAL: STEP_BINDING}
        else [f"the check step does not bind exactly {CREDENTIAL}: {STEP_BINDING}"]
    )
    if _normalized(check.get("run")) != CHECK_COMMAND:
        found.append(f"the check step does not run exactly {CHECK_COMMAND!r}")
    if "if" in check:
        found.append("the check step has if:")
    stray = _stray_mentions(check, {f"env.{CREDENTIAL}", "run"})
    return found + [f"{CREDENTIAL} also reached at check step {path}" for path in stray]


def _upload_violations(step: dict[object, object]) -> list[str]:
    """Return why the upload step does not take the token as its input alone.

    Returns
    -------
    list[str]
        One entry per violation.
    """
    inputs = step.get("with")
    passed = inputs.get("access-token") if isinstance(inputs, dict) else None
    found = (
        []
        if _normalized(passed) == ACCESS_TOKEN_INPUT
        else [f"the upload step does not pass access-token: {ACCESS_TOKEN_INPUT}"]
    )
    stray = _stray_mentions(step, {"with.access-token"})
    found += [
        f"the upload step's {path} holds {CREDENTIAL}; the composite action "
        f"passes its env to nested steps"
        if path.startswith("env")
        else f"{CREDENTIAL} also reached at upload step {path}"
        for path in stray
    ]
    return found


def binding_violations(document: Document, step: dict[object, object]) -> list[str]:
    """Return why the token is not bound on the check step and passed as input.

    Returns
    -------
    list[str]
        One entry per violation.
    """
    check = check_step(document, step)
    held = [step] if check is None else [step, check]
    elsewhere = [
        scalar.path
        for scalar in scalars({**document, "jobs": _without(document, held)})
        if CREDENTIAL.casefold() in scalar.text.casefold()
    ]
    return (
        _check_violations(check)
        + _upload_violations(step)
        + [f"{CREDENTIAL} also reached at {path}" for path in elsewhere]
    )


def _without(
    document: Document, removed: list[dict[object, object]]
) -> dict[str, object]:
    """Return a document's jobs with some steps removed, for sweeping the rest.

    Returns
    -------
    dict[str, object]
        Job name to job, without those steps.
    """
    return {
        name: {
            **job,
            "steps": [
                other
                for other in steps(job)
                if not any(other is gone for gone in removed)
            ],
        }
        for name, job in jobs(document).items()
    }


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
    abandons both its upload and its baseline write. Every group, at
    workflow and job level, must evaluate ``${{ github.ref }}`` and
    ``${{ github.event_name }}``: a dispatch from another branch would
    otherwise join main's group, replace main's pending run, and then
    skip the ref-guarded upload, so that main commit never publishes;
    and a dispatch on main would replace a pending push without writing
    the baseline, which only a push saves. A constant group at either level
    collides, whatever the other level is keyed on.

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
    return _unkeyed_groups(scopes) + _cancelling_scopes(scopes)


def _unkeyed_groups(scopes: list[dict[object, object]]) -> list[str]:
    """Return the concurrency groups that do not evaluate the ref and event.

    Returns
    -------
    list[str]
        One entry per unkeyed group.
    """
    return [
        f"concurrency group {scope['group']!r} is not keyed on the ref and event"
        for scope in scopes
        if "group" in scope
        and not all(key.search(str(scope["group"])) for key in _GROUP_KEYS)
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
        if _normalized(scope.get("cancel-in-progress", "false")) != "false"
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
    return _normalized(item.get("continue-on-error", "false")) != "false"


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
