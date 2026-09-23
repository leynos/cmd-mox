"""Follow local ``uses:`` references from the pull-request lanes.

Generic: nothing here knows about CodeScene. A workflow declaring only
``workflow_call`` still runs on a pull request when a pull-request job
calls it, and ``secrets: inherit`` hands it every secret the caller
holds; a local composite action runs inside the calling job. So a rule
about what a pull request can reach is a rule over this closure, not
over the workflows whose triggers name a pull request.
"""

from __future__ import annotations

import typing as typ

from tests.helpers.workflow_reading import (
    ACTION_FILES,
    WORKFLOW_DIRECTORY,
    WorkflowReadingError,
    filter_names,
    triggers,
    uses_references,
)

if typ.TYPE_CHECKING:
    from tests.helpers.workflow_reading import Document

#: Triggers that put a workflow on the pull-request surface. The
#: ``pull_request*`` family and ``merge_group`` run a pull request's code
#: or run for it. ``workflow_run`` is included because a workflow chained
#: onto a pull-request workflow runs for that pull request, and reading
#: which workflows it names would be one more reading to get wrong.
#: ``issue_comment`` fires for comments on pull requests, holds the
#: repository's secrets, and is often written to check out the pull
#: request's head, so it is included rather than argued away. A push to
#: a branch other than main is read separately, by
#: ``_pushes_other_branches``.
PULL_REQUEST_TRIGGERS: typ.Final[frozenset[str]] = frozenset({
    "issue_comment",
    "merge_group",
    "pull_request",
    "pull_request_review",
    "pull_request_review_comment",
    "pull_request_target",
    "workflow_run",
})

#: The two same-repository spellings: ``./`` and the documented ``$/``.
_LOCAL_PREFIXES: typ.Final[tuple[str, ...]] = ("./", "$/")


def refusal(reference: str, repository: str) -> str | None:
    """Return why a ``uses:`` reference cannot be followed, if it cannot.

    A qualified self-call (``owner/repo/.github/workflows/x.yml@ref``)
    runs the file as it stands at that ref, not the checked-out one, so
    following the local file would prove nothing about what runs. A local
    spelling carrying ``@ref`` is not a local call GitHub accepts.

    Parameters
    ----------
    reference : str
        The ``uses:`` value.
    repository : str
        This repository as ``owner/name``.

    Returns
    -------
    str or None
        The reason, or ``None`` when the reference may be followed.

    Examples
    --------
    >>> refusal("leynos/x/.github/workflows/a.yml@main", "leynos/x")
    'a qualified self-call runs at its ref, not at this checkout'
    >>> refusal("./.github/workflows/a.yml", "leynos/x") is None
    True
    """
    if reference.casefold().startswith(f"{repository.casefold()}/"):
        return "a qualified self-call runs at its ref, not at this checkout"
    if reference.startswith(_LOCAL_PREFIXES) and "@" in reference:
        return "a local reference cannot name a ref"
    return None


def _local_path(reference: str) -> str | None:
    """Return a local reference's repository-relative path, if it is local.

    Returns
    -------
    str or None
        The path without its local prefix, or ``None`` for a remote one.
    """
    for prefix in _LOCAL_PREFIXES:
        if reference.startswith(prefix):
            return reference.removeprefix(prefix).rstrip("/")
    return None


def _candidates(path: str) -> list[str]:
    """Return the document keys a local path can name.

    Returns
    -------
    list[str]
        The path itself under the workflow directory, otherwise the
        action metadata files of the directory it names.
    """
    if path.startswith(WORKFLOW_DIRECTORY):
        return [path]
    return [f"{path}/{name}" for name in ACTION_FILES]


def _local_targets(
    document: Document, documents: dict[str, Document], repository: str
) -> list[str]:
    """Return the local documents one document's ``uses:`` references run.

    Returns
    -------
    list[str]
        Their keys, in reference order.
    """
    targets = (
        local_target(reference, documents, repository)
        for reference in uses_references(document)
    )
    return [target for target in targets if target is not None]


def local_target(
    reference: str, documents: dict[str, Document], repository: str
) -> str | None:
    """Return the document a local ``uses:`` reference runs, if it is local.

    Matched by shape: strip the local prefix, then a path under the
    workflow directory is a reusable workflow and any other path is a
    composite action's directory.

    Parameters
    ----------
    reference : str
        The ``uses:`` value.
    documents : dict[str, Document]
        Every document, by repository-relative path.
    repository : str
        This repository as ``owner/name``.

    Returns
    -------
    str or None
        The document's key, or ``None`` for a remote or refused reference.

    Raises
    ------
    WorkflowReadingError
        If a local reference names nothing in this tree.
    """
    path = _local_path(reference)
    if path is None or refusal(reference, repository):
        return None
    found = [candidate for candidate in _candidates(path) if candidate in documents]
    if not found:
        msg = f"{reference!r} names nothing in this tree"
        raise WorkflowReadingError(msg)
    return found[0]


def _pushes_other_branches(filters: object) -> bool:
    """Return whether a push trigger fires for a branch other than main.

    A pull request's head branch in this repository is pushed before the
    pull request runs anything, so a workflow answering that push runs the
    same code with the repository's secrets. Only an exact ``[main]``
    branch filter or a tags-only filter keeps a push off the surface;
    every other shape, ``branches-ignore`` included, is read as reaching
    other branches, since failing closed is the safe direction here.

    Returns
    -------
    bool
        False only for a main-only or tags-only push filter.
    """
    if not isinstance(filters, dict):
        return True
    if "branches" in filters:
        return filter_names(filters["branches"]) != ["main"]
    return "branches-ignore" in filters or not {"tags", "tags-ignore"} & set(filters)


def _serves_pull_requests(document: Document) -> bool:
    """Return whether a workflow's own triggers run pull-request code.

    Returns
    -------
    bool
        True for a pull-request trigger or a push beyond main.
    """
    declared = triggers(document)
    if PULL_REQUEST_TRIGGERS & declared.keys():
        return True
    return "push" in declared and _pushes_other_branches(declared["push"])


def pull_request_seeds(documents: dict[str, Document]) -> list[str]:
    """Return the workflows whose own triggers serve a pull request.

    Returns
    -------
    list[str]
        Their keys.

    Raises
    ------
    WorkflowReadingError
        If there are none, which cannot be true of a repository with a
        pull-request lane and means the trigger reader is broken.
    """
    seeds = [
        name
        for name, document in documents.items()
        if name.startswith(WORKFLOW_DIRECTORY) and _serves_pull_requests(document)
    ]
    if not seeds:
        msg = "no workflow serves a pull request; the trigger reader is broken"
        raise WorkflowReadingError(msg)
    return seeds


def pull_request_surface(
    documents: dict[str, Document], repository: str
) -> dict[str, Document]:
    """Return every document a pull request can run, transitively.

    Parameters
    ----------
    documents : dict[str, Document]
        Every document, by repository-relative path.
    repository : str
        This repository as ``owner/name``.

    Returns
    -------
    dict[str, Document]
        The seeds and every local workflow and action they reach.
    """
    found: dict[str, Document] = {}
    pending = pull_request_seeds(documents)
    while pending:
        name = pending.pop()
        if name not in found:
            found[name] = documents[name]
            pending.extend(_local_targets(documents[name], documents, repository))
    return found


def refused_references(surface: dict[str, Document], repository: str) -> list[str]:
    """Return every reference on the surface that cannot be followed.

    Returns
    -------
    list[str]
        ``document: reference (reason)`` for each.
    """
    return [
        f"{name}: {reference} ({reason})"
        for name, document in surface.items()
        for reference in uses_references(document)
        if (reason := refusal(reference, repository))
    ]
