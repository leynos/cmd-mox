"""Constructed cases proving each CV-005 pull-request clause and reader.

The repository's own workflows use one spelling of everything, so a
clause that reads them cannot tell a working rule from a broken one.
Each case drives the production query the repository contract calls
over a document built to hold one breach, and requires the named site to
be found; the compliant closure is driven too, so a rule refusing
everything fails here as well. The publisher's cases are in
``test_codescene_publisher_cases.py``.
"""

from __future__ import annotations

import typing as typ

import pytest
import yaml

from tests.helpers.codescene_reach import pull_request_breaches
from tests.helpers.workflow_closure import (
    pull_request_surface,
    refused_references,
)
from tests.helpers.workflow_reading import (
    WorkflowReadingError,
    load_document,
    read_workflow_tree,
    triggers,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from tests.helpers.workflow_reading import Document

REPOSITORY = "leynos/example"

CALLER = """\
on:
  pull_request:
jobs:
  call:
    uses: ./.github/workflows/probe.yml
"""

PROBE = """\
on:
  workflow_call:
jobs:
  probe:
    runs-on: ubuntu-latest
    steps:
      - run: echo probe
"""


def _surface_breaches(caller: str, probe: str) -> list[str]:
    """Return the breaches the production query finds from ``caller``.

    Returns
    -------
    list[str]
        Every breach and refused reference on the surface.
    """
    documents = {
        ".github/workflows/ci.yml": load_document(caller),
        ".github/workflows/probe.yml": load_document(probe),
        ".github/actions/local/action.yml": load_document(
            "runs:\n  using: composite\n  steps:\n    - run: echo local\n"
        ),
    }
    surface = pull_request_surface(documents, REPOSITORY)
    return pull_request_breaches(surface) + refused_references(surface, REPOSITORY)


def test_the_compliant_closure_passes() -> None:
    """Every pull-request rule accepts the documents the breaches start from."""
    assert not _surface_breaches(CALLER, PROBE), "the compliant closure breaches"


@pytest.mark.parametrize(
    ("caller", "probe", "expected"),
    [
        pytest.param(
            CALLER.replace("probe.yml", "probe.yml\n    secrets: inherit"),
            PROBE.replace(
                "echo probe",
                'curl -H "$CS_ACCESS_TOKEN" https://api.codescene.io/v2/projects',
            ),
            "probe.yml: host",
            id="sweep-1-workflow-call-inherit-curl",
        ),
        pytest.param(
            CALLER.replace("./.github", "$/.github"),
            PROBE.replace("echo probe", "curl https://api.codescene.io"),
            "probe.yml: host",
            id="sweep-1-dollar-spelling",
        ),
        pytest.param(
            CALLER.replace("./.github", "leynos/example/.github").replace(
                "probe.yml", "probe.yml@main"
            ),
            PROBE,
            "qualified self-call",
            id="sweep-1-qualified-self-call",
        ),
        pytest.param(
            CALLER.replace("probe.yml", "probe.yml@main").replace("./", "$/"),
            PROBE,
            "cannot name a ref",
            id="sweep-1-dollar-with-ref",
        ),
        pytest.param(
            CALLER,
            PROBE.replace("echo probe", "echo ${{ secrets.CS_ACCESS_TOKEN }}"),
            "probe.yml: token: jobs.probe.steps[0].run",
            id="sweep-2-run-body",
        ),
        pytest.param(
            CALLER,
            PROBE.replace(
                "- run: echo probe",
                "- uses: x/y@z\n        with:\n"
                "          payload: ${{ secrets.cs_access_token }}",
            ),
            "probe.yml: token: jobs.probe.steps[0].with.payload",
            id="sweep-2-action-input-any-case",
        ),
        pytest.param(
            CALLER,
            PROBE.replace(
                "- run: echo probe",
                "- run: echo\n        env:\n          ANY: ${{ toJSON(secrets) }}",
            ),
            "whole secrets context",
            id="sweep-2-whole-secrets-context",
        ),
        pytest.param(
            CALLER.replace(
                "probe.yml",
                "probe.yml\n    secrets:\n      T: ${{ secrets.CS_ACCESS_TOKEN }}",
            ),
            PROBE,
            "ci.yml: token: jobs.call.secrets.T",
            id="sweep-2-named-forward",
        ),
        pytest.param(
            CALLER.replace(
                "uses: ./.github/workflows/probe.yml",
                "uses: x/y/.github/workflows/z.yml@a",
            )
            + "    secrets: inherit\n",
            PROBE,
            "secrets: inherit",
            id="sweep-2-inherit-to-remote",
        ),
        pytest.param(
            CALLER.replace(
                "jobs:",
                "defaults:\n  run:\n    shell: curl API.CODESCENE.IO; bash {0}\njobs:",
            ),
            PROBE,
            "ci.yml: host: defaults.run.shell",
            id="sweep-7-defaults-run-shell",
        ),
        pytest.param(
            CALLER,
            PROBE.replace(
                "  workflow_call:",
                "  workflow_call:\n    secrets:\n"
                "      CS_ACCESS_TOKEN:\n        required: false",
            ),
            "token: on.workflow_call.secrets.CS_ACCESS_TOKEN",
            id="sweep-7-workflow-call-secret-declaration",
        ),
        pytest.param(
            CALLER,
            PROBE.replace("echo probe", "echo ready && cs-coverage check"),
            "cs-coverage",
            id="cli-in-a-command-list",
        ),
        pytest.param(
            CALLER,
            PROBE.replace(
                "- run: echo probe",
                "- uses: leynos/shared-actions/.github/actions/"
                "upload-codescene-coverage@a",
            ),
            "upload action",
            id="upload-action-in-callee",
        ),
    ],
)
def test_the_closure_finds_every_road_to_codescene(
    caller: str, probe: str, expected: str
) -> None:
    """Each breach, placed where only the closure reaches it, is found.

    The expected site is named rather than "some breach": each case is a
    proof of one clause, and a case another clause happened to catch
    would pass with the clause under test deleted.
    """
    breaches = _surface_breaches(caller, probe)
    assert any(expected in breach for breach in breaches), (
        f"{expected!r} not among {breaches}"
    )


def test_a_local_action_on_the_surface_is_read() -> None:
    """A composite action a pull-request job runs is part of the surface."""
    documents = {
        ".github/workflows/ci.yml": load_document(
            "on: pull_request\njobs:\n  a:\n    steps:\n"
            "      - uses: ./.github/actions/local\n"
        ),
        ".github/actions/local/action.yml": load_document(
            "runs:\n  using: composite\n  steps:\n"
            "    - run: curl codescene.io\n      shell: bash\n"
        ),
    }
    breaches = pull_request_breaches(pull_request_surface(documents, REPOSITORY))
    assert breaches, "the local action's contact was not found"


def test_a_local_reference_to_nothing_is_refused() -> None:
    """A reference the closure cannot resolve fails rather than vanishing."""
    documents = {".github/workflows/ci.yml": load_document(CALLER)}
    with pytest.raises(WorkflowReadingError, match="names nothing"):
        pull_request_surface(documents, REPOSITORY)


def test_a_duplicate_key_is_refused() -> None:
    """PyYAML keeps the last duplicate silently; the loader refuses it."""
    text = "jobs:\n  a:\n    runs-on: paid-label\n    runs-on: ubuntu-latest\n"
    with pytest.raises(WorkflowReadingError, match="duplicate key"):
        load_document(text)


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param(
            load_document("on: pull_request\n"), {"pull_request"}, id="scalar"
        ),
        pytest.param(
            load_document("on: [push, pull_request]\n"),
            {"push", "pull_request"},
            id="sequence",
        ),
        pytest.param(
            load_document("on:\n  push:\n  pull_request:\n"),
            {"push", "pull_request"},
            id="mapping",
        ),
        pytest.param(
            yaml.safe_load("on: [pull_request]\n"), {"pull_request"}, id="boolean-key"
        ),
    ],
)
def test_every_trigger_form_is_read(
    document: dict[object, object], expected: set[str]
) -> None:
    """Scalar, sequence, and mapping forms, under either key, are read."""
    assert set(triggers(document)) == expected, f"read {triggers(document)}"


def test_both_trigger_keys_are_refused() -> None:
    """GitHub merges ``on`` and ``True``; a reader choosing one is blind."""
    document: dict[object, object] = {"on": "push", True: "pull_request"}
    with pytest.raises(WorkflowReadingError, match="both"):
        triggers(document)


def _entry_surface(trigger: str) -> dict[str, Document]:
    """Return the surface of a tree whose ``entry.yml`` curls CodeScene.

    ``entry.yml`` declares ``trigger`` and nothing calls it, so it is on
    the surface only if its own trigger makes it a seed.

    Returns
    -------
    dict[str, Document]
        The pull-request surface of the tree.
    """
    documents = {
        ".github/workflows/ci.yml": load_document(CALLER.replace("probe.yml", "x.yml")),
        ".github/workflows/x.yml": load_document(PROBE),
        ".github/workflows/entry.yml": load_document(
            PROBE.replace("workflow_call:", trigger).replace(
                "echo probe", "curl codescene.io"
            )
        ),
    }
    return pull_request_surface(documents, REPOSITORY)


@pytest.mark.parametrize(
    "trigger",
    [
        pytest.param("workflow_run:", id="workflow-run"),
        pytest.param("issue_comment:", id="issue-comment"),
        pytest.param("merge_group:", id="merge-group"),
        pytest.param("pull_request_review:", id="pull-request-review"),
        pytest.param("pull_request_review_comment:", id="review-comment"),
        pytest.param("push:", id="push-every-branch"),
        pytest.param("push:\n    paths: ['src/**']", id="push-paths-only"),
        pytest.param("push:\n    branches: [dev]", id="push-other-branch"),
        pytest.param("push:\n    branches-ignore: [main]", id="push-branches-ignore"),
        pytest.param(
            "push:\n    branches-ignore: [main]\n    tags: ['v*']",
            id="push-branches-ignore-with-tags",
        ),
    ],
)
def test_every_pull_request_entry_point_is_a_seed(trigger: str) -> None:
    """A workflow on any trigger that runs pull-request code is on the surface."""
    breaches = pull_request_breaches(_entry_surface(trigger))
    assert any("entry.yml: host" in breach for breach in breaches), breaches


@pytest.mark.parametrize(
    "trigger",
    [
        pytest.param("push:\n    branches: [main]", id="push-main"),
        pytest.param("push:\n    tags: ['v*']", id="push-tags"),
        pytest.param("schedule:\n    - cron: '0 0 * * *'", id="schedule"),
    ],
)
def test_main_only_entry_points_stay_off_the_surface(trigger: str) -> None:
    """The publisher's own trigger shapes are not read as pull-request ones.

    Without this half, a seed reading that swept every workflow onto the
    surface would pass the case above and condemn the publisher.
    """
    surface = _entry_surface(trigger)
    assert ".github/workflows/entry.yml" not in surface, sorted(surface)


def test_an_unlistable_workflow_directory_is_refused(tmp_path: Path) -> None:
    """A tree whose workflows cannot be listed raises rather than reads empty."""
    with pytest.raises(WorkflowReadingError, match="could not be listed"):
        read_workflow_tree(tmp_path)


def test_an_empty_workflow_directory_is_refused(tmp_path: Path) -> None:
    """A tree with no workflow is the reader failing, not a compliant repository."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    with pytest.raises(WorkflowReadingError, match="no workflow was read"):
        read_workflow_tree(tmp_path)


def test_an_undecodable_workflow_is_refused(tmp_path: Path) -> None:
    """A workflow that is not UTF-8 is refused by name at the boundary."""
    directory = tmp_path / ".github" / "workflows"
    directory.mkdir(parents=True)
    (directory / "ci.yml").write_bytes(b"on: push\n\xff\xfe\n")
    with pytest.raises(WorkflowReadingError, match=r"ci\.yml"):
        read_workflow_tree(tmp_path)
