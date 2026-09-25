"""Prove the `codescene` environment sits on the uploading job and nowhere else.

Each test mutates a copy of this repository's workflows the way a later edit
could, and asserts the clause meant to catch it does. The check step, the ref
guard and `access-token:` stay held by the CV-005 coverage contract.
"""

from __future__ import annotations

import copy
import typing as typ
from pathlib import Path

import pytest

from tests.helpers.codescene_environment import (
    MISSING,
    REACHABLE,
    STRAY,
    environment_violations,
)
from tests.helpers.workflow_reading import jobs, read_workflow_tree

if typ.TYPE_CHECKING:
    from tests.helpers.workflow_reading import Document

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "leynos/cmd-mox"
PUBLISHER = ".github/workflows/coverage-main.yml"
LANE = ".github/workflows/ci.yml"

pytestmark = pytest.mark.skipif(
    not (REPOSITORY_ROOT / ".github" / "workflows").is_dir(),
    reason="workflows not present in this working copy",
)


@pytest.fixture
def documents() -> dict[str, Document]:
    """Return a private copy of the repository's workflows to mutate.

    Returns
    -------
    dict[str, Document]
        The parsed workflows, deep-copied for this test alone.
    """
    return copy.deepcopy(read_workflow_tree(REPOSITORY_ROOT))


def _first_job(documents: dict[str, Document], name: str) -> dict[object, object]:
    """Return one workflow's first job.

    Returns
    -------
    dict[object, object]
        The job mapping, for mutation in place.
    """
    return next(iter(jobs(documents[name]).values()))


def _found(documents: dict[str, Document]) -> list[str]:
    """Return the rule's findings for these documents.

    Returns
    -------
    list[str]
        The violations the environment rule reports.
    """
    return environment_violations(documents, REPOSITORY)


def _reports(documents: dict[str, Document], fragment: str) -> None:
    """Fail unless the rule reports a violation containing ``fragment``."""
    found = _found(documents)
    assert any(fragment in problem for problem in found), (
        f"expected a violation naming {fragment!r}, got {found}"
    )


def test_repository_places_the_environment(documents: dict[str, Document]) -> None:
    """The publisher declares the environment and nothing else does."""
    found = _found(documents)
    assert not found, f"expected no violations, got {found}"


def test_publisher_cannot_drop_the_environment(documents: dict[str, Document]) -> None:
    """Without it the moved token never reaches the upload, which then skips."""
    del _first_job(documents, PUBLISHER)["environment"]
    _reports(documents, MISSING)


def test_publisher_cannot_name_another_environment(
    documents: dict[str, Document],
) -> None:
    """Another environment holds no CodeScene token."""
    _first_job(documents, PUBLISHER)["environment"] = "production"
    _reports(documents, MISSING)


def test_mapping_form_is_accepted(documents: dict[str, Document]) -> None:
    """`{name: codescene}` is the same declaration as the bare string."""
    _first_job(documents, PUBLISHER)["environment"] = {"name": "codescene"}
    found = _found(documents)
    assert not found, f"the mapping form must be accepted, got {found}"


def test_no_other_job_may_declare_it(documents: dict[str, Document]) -> None:
    """A second holder of the token widens what can read it."""
    declared = typ.cast("dict[object, object]", documents[PUBLISHER]["jobs"])
    declared["other"] = {
        "runs-on": "ubuntu-latest",
        "environment": "codescene",
        "steps": [{"run": "true"}],
    }
    _reports(documents, STRAY)


def test_no_pull_request_job_may_declare_it(documents: dict[str, Document]) -> None:
    """A pull request's own code must never be able to request the token."""
    _first_job(documents, LANE)["environment"] = {"name": "codescene"}
    _reports(documents, REACHABLE)


def test_an_empty_reading_is_refused(documents: dict[str, Document]) -> None:
    """With no uploader left the rule says so rather than passing."""
    job = _first_job(documents, PUBLISHER)
    job["steps"] = [
        step
        for step in typ.cast("list[dict[object, object]]", job["steps"])
        if "upload-codescene-coverage" not in str(step.get("uses", ""))
    ]
    _reports(documents, "no workflow job invokes")
