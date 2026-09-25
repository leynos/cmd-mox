"""Hold the CodeScene token's environment to the uploading job (CV-005).

The token lives in the `codescene` environment, whose deployment policy admits
`main` alone. So every workflow job that invokes the uploader declares that
environment, no other job does, and nothing a pull request can run declares it
in any job: a declaration there would let branch code ask for the token.
"""

from __future__ import annotations

import typing as typ

from tests.helpers.codescene_reach import UPLOAD_ACTION
from tests.helpers.workflow_closure import pull_request_surface
from tests.helpers.workflow_reading import WORKFLOW_DIRECTORY, jobs, steps

if typ.TYPE_CHECKING:
    from tests.helpers.workflow_reading import Document

ENVIRONMENT: typ.Final[str] = "codescene"
MISSING: typ.Final[str] = f"the uploading job must declare `environment: {ENVIRONMENT}`"
STRAY: typ.Final[str] = f"declares `{ENVIRONMENT}` but uploads nothing"
REACHABLE: typ.Final[str] = (
    f"is reachable from a pull request and declares `{ENVIRONMENT}`"
)


def environment_name(job: dict[object, object]) -> str | None:
    """Return the environment a job declares, from either accepted form.

    Returns
    -------
    str | None
        The environment's name, or None when the job declares none.

    Examples
    --------
    >>> environment_name({"environment": "codescene"})
    'codescene'
    >>> environment_name({"environment": {"name": "codescene", "url": "x"}})
    'codescene'
    >>> environment_name({}) is None
    True
    """
    match job.get("environment"):
        case str() as name:
            return name
        case {"name": str() as name}:
            return name
        case _:
            return None


def _uploads(job: dict[object, object]) -> bool:
    """Return whether a job has a step invoking the uploader.

    Returns
    -------
    bool
        True when some step's ``uses`` names the upload action.
    """
    return any(
        UPLOAD_ACTION.casefold() in str(step.get("uses", "")).casefold()
        for step in steps(job)
    )


def _workflow_jobs(
    documents: dict[str, Document],
) -> list[tuple[str, dict[object, object]]]:
    """Return every job in every workflow with its location.

    Returns
    -------
    list[tuple[str, dict[object, object]]]
        ``"path:job"`` and the job, for each workflow job.
    """
    return [
        (f"{name}:{job_id}", job)
        for name, document in documents.items()
        if name.startswith(WORKFLOW_DIRECTORY)
        for job_id, job in jobs(document).items()
    ]


def environment_violations(
    documents: dict[str, Document], repository: str
) -> list[str]:
    """Report every departure from the `codescene` environment placement.

    Returns
    -------
    list[str]
        One message per violation; empty when the placement holds.
    """
    placed = _workflow_jobs(documents)
    uploading = [(where, job) for where, job in placed if _uploads(job)]
    if not uploading:
        return ["no workflow job invokes the CodeScene uploader"]
    problems = [
        f"{where}: {MISSING}"
        for where, job in uploading
        if environment_name(job) != ENVIRONMENT
    ]
    problems.extend(
        f"{where} {STRAY}"
        for where, job in placed
        if not _uploads(job) and environment_name(job) == ENVIRONMENT
    )
    surface = pull_request_surface(documents, repository)
    problems.extend(
        f"{where} {REACHABLE}"
        for where, job in _workflow_jobs(surface)
        if environment_name(job) == ENVIRONMENT
    )
    return problems
