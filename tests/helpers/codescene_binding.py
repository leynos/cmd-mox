"""Where the CodeScene token may be bound in the publisher, as readings.

Separated from ``codescene_publisher``, whose subject is the publisher's
shape as a whole, so neither module outgrows the 400-line limit. The
subject here is the token: a check step binds it and reports only
whether it is set, and the upload takes it as its ``access-token``
input and nowhere else. The upload action is composite and hands its
step ``env`` to nested upload-artifact and cache steps, so the token is
refused there.
"""

from __future__ import annotations

import typing as typ

from tests.helpers.codescene_reach import CREDENTIAL, normalized
from tests.helpers.workflow_reading import jobs, scalars, steps

if typ.TYPE_CHECKING:
    from tests.helpers.workflow_reading import Document

#: The id of the step that reports whether the token is available. The
#: token is in no step's ``env``: the upload action is composite and
#: hands its step ``env`` to nested upload-artifact and cache steps,
#: while binding the token itself from ``access-token``. So a separate
#: step publishes only whether the token is set.
CHECK_STEP_ID: typ.Final[str] = "codescene-token"

#: The check step's sole command, exactly. The expression evaluates to
#: ``true`` or ``false`` before the shell runs, so the check binds
#: nothing and needs no shell conditional. Held whole because
#: ``false && X`` contains ``X``: a looser match accepts a command that
#: never reports, and the upload then skips forever.
CHECK_COMMAND: typ.Final[str] = (
    f'echo "available=${{{{ secrets.{CREDENTIAL} != \'\' }}}}" >> "$GITHUB_OUTPUT"'
)

#: The upload's ``access-token`` input, the one place the token is passed.
ACCESS_TOKEN_INPUT: typ.Final[str] = f"${{{{ secrets.{CREDENTIAL} }}}}"


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
    """Return why the check step does not report the token's presence alone.

    Returns
    -------
    list[str]
        One entry per violation.
    """
    if check is None:
        return [f"no step with id {CHECK_STEP_ID!r} precedes the upload in its job"]
    found = ["the check step declares env; it binds nothing"] if "env" in check else []
    if normalized(check.get("run")) != CHECK_COMMAND:
        found.append(f"the check step does not run exactly {CHECK_COMMAND!r}")
    if "if" in check:
        found.append("the check step has if:")
    stray = _stray_mentions(check, {"run"})
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
        if normalized(passed) == ACCESS_TOKEN_INPUT
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
