"""Constructed publisher cases proving each CV-005 publisher clause.

The repository's own publisher uses one spelling of everything, so a
clause that reads it cannot tell a working rule from a broken one. Each
case mutates a compliant publisher once and requires the named clause to
report it; the compliant publisher is driven too, so a rule refusing
everything fails here as well.
"""

from __future__ import annotations

import pytest

from tests.helpers.codescene_publisher import (
    binding_violations,
    concurrency_violations,
    guard_violations,
    input_violations,
    neutralized_steps,
    publisher,
    swallowed_failures,
    trigger_violations,
    upload_step,
)
from tests.helpers.codescene_reach import retired_names
from tests.helpers.workflow_reading import WorkflowReadingError, load_document

#: A full-length commit pin, as the publisher rules require.
PIN = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

PUBLISHER = """\
on:
  push:
    branches: [main]
concurrency:
  group: coverage-main-${{ github.ref }}-${{ github.event_name }}
  cancel-in-progress: false
jobs:
  publish:
    runs-on: ubuntu-latest
    steps:
      - name: Generate coverage
        uses: leynos/shared-actions/.github/actions/generate-coverage@<pin>
        with:
          with-ratchet: 'true'
      - name: Upload
        env:
          CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}
        if: env.CS_ACCESS_TOKEN != '' && github.ref == 'refs/heads/main'
        uses: leynos/shared-actions/.github/actions/upload-codescene-coverage@<pin>
        with:
          mode: upload
          access-token: ${{ env.CS_ACCESS_TOKEN }}
""".replace("<pin>", PIN)


def _publisher_violations(text: str) -> list[str]:
    """Return every publisher violation in one constructed workflow.

    Returns
    -------
    list[str]
        The violations every publisher reading reports.
    """
    document = load_document(text)
    step = upload_step(document)
    return (
        trigger_violations(document)
        + guard_violations(step)
        + binding_violations(document, step)
        + concurrency_violations(document)
        + neutralized_steps(document)
        + input_violations(step)
        + swallowed_failures(document)
    )


def test_the_compliant_publisher_passes() -> None:
    """Every publisher rule accepts the document the mutations start from."""
    assert not _publisher_violations(PUBLISHER), "the compliant publisher fails"


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        pytest.param(
            "github.ref == 'refs/heads/main'",
            "github.ref == 'refs/heads/main' && github.actor != 'x' "
            "|| github.event_name == 'workflow_dispatch'",
            "guard",
            id="sweep-3-hidden-disjunction",
        ),
        pytest.param(
            "github.ref == 'refs/heads/main'",
            "github.ref == 'refs/heads/main' && false",
            "guard",
            id="sweep-3-neutralizing-conjunct",
        ),
        pytest.param(
            " && github.ref == 'refs/heads/main'",
            "",
            "guard",
            id="sweep-3-ref-guard-deleted",
        ),
        pytest.param(
            "  cancel-in-progress: false",
            "  cancel-in-progress: true",
            "cancel-in-progress 'true'",
            id="sweep-6-cancel",
        ),
        pytest.param(
            "group: coverage-main-${{ github.ref }}-${{ github.event_name }}",
            "group: coverage-main",
            "is not keyed on",
            id="sweep-6-group-shared-across-refs",
        ),
        pytest.param(
            "group: coverage-main-${{ github.ref }}-${{ github.event_name }}",
            "group: coverage-main-github.ref-github.event_name",
            "is not keyed on",
            id="sweep-6-group-names-the-ref-unevaluated",
        ),
        pytest.param(
            "group: coverage-main-${{ github.ref }}-${{ github.event_name }}",
            "group: coverage-main-${{ github.ref }}",
            "is not keyed on",
            id="sweep-6-group-shared-by-push-and-dispatch",
        ),
        pytest.param(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    concurrency:\n      group: publish\n",
            "'publish' is not keyed on",
            id="sweep-6-constant-job-group",
        ),
        pytest.param(
            f"upload-codescene-coverage@{PIN}",
            "upload-codescene-coverage@main",
            "is not pinned to a commit",
            id="upload-action-unpinned",
        ),
        pytest.param(
            "concurrency:\n"
            "  group: coverage-main-${{ github.ref }}-${{ github.event_name }}\n"
            "  cancel-in-progress: false\n",
            "",
            "no workflow-level concurrency group",
            id="sweep-6-no-group",
        ),
        pytest.param(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    concurrency:\n"
            "      group: x-${{ github.ref }}-${{ github.event_name }}\n"
            "      cancel-in-progress: true\n",
            "cancel-in-progress 'true'",
            id="sweep-6-job-level-cancel",
        ),
        pytest.param(
            "        env:\n          CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}\n",
            "",
            "does not bind",
            id="sweep-8-binding-deleted",
        ),
        pytest.param(
            "CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}",
            "CS_ACCESS_TOKEN: ''",
            "does not bind",
            id="sweep-8-binding-emptied",
        ),
        pytest.param(
            "          access-token: ${{ env.CS_ACCESS_TOKEN }}\n",
            "",
            "does not pass access-token",
            id="sweep-8-access-token-deleted",
        ),
        pytest.param(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    env:\n"
            "      CS_ACCESS_TOKEN: ${{ secrets.CS_ACCESS_TOKEN }}\n",
            "also reached at jobs.publish.env",
            id="sweep-8-token-also-in-job-env",
        ),
        pytest.param(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    if: false\n",
            "job publish has if:",
            id="sweep-8-job-condition",
        ),
        pytest.param(
            "        with:\n          with-ratchet",
            "        if: false\n        with:\n          with-ratchet",
            "coverage step",
            id="sweep-8-coverage-step-condition",
        ),
        pytest.param(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    continue-on-error: true\n",
            "job publish has continue-on-error",
            id="sweep-8-job-swallows-failure",
        ),
        pytest.param(
            "      - name: Upload\n",
            "      - name: Upload\n        continue-on-error: true\n",
            "step 'Upload' has continue-on-error",
            id="sweep-8-upload-swallows-failure",
        ),
        pytest.param(
            "      - name: Generate coverage\n",
            "      - name: Generate coverage\n        continue-on-error: true\n",
            "step 'Generate coverage' has continue-on-error",
            id="sweep-8-coverage-swallows-failure",
        ),
        pytest.param(
            "branches: [main]",
            "branches: ['**']",
            "push branches",
            id="push-every-branch",
        ),
        pytest.param(
            "    branches: [main]\n",
            "    tags: ['v*']\n",
            "push filter",
            id="push-tags-only",
        ),
        pytest.param(
            "on:\n",
            "on:\n  pull_request:\n",
            "trigger pull_request",
            id="publisher-serves-pull-requests",
        ),
    ],
)
def test_each_publisher_mutation_is_refused(old: str, new: str, expected: str) -> None:
    """Every mutation of the compliant publisher is reported by its clause.

    The guard is held to an exact conjunct set, so the hidden disjunction
    (``<guard> && github.actor != 'x' || dispatch``) fails because its
    last conjunct is not one of the required terms. No separate ``||``
    refusal exists: under exact equality it could never fire first, and
    an unreachable check would read as protection it does not add.
    """
    assert old in PUBLISHER, f"the mutation's anchor {old!r} is missing"
    violations = _publisher_violations(PUBLISHER.replace(old, new, 1))
    assert any(expected in violation for violation in violations), (
        f"{expected!r} not among {violations}"
    )


@pytest.mark.parametrize(
    "extra",
    [
        pytest.param(".github/workflows/second.yml", id="second-workflow"),
        pytest.param(".github/actions/wrap/action.yml", id="local-action"),
    ],
)
def test_a_second_uploader_is_refused(extra: str) -> None:
    """The publisher is found, so a second uploader cannot hide beside it."""
    documents = {
        ".github/workflows/main.yml": load_document(PUBLISHER),
        extra: load_document(PUBLISHER),
    }
    with pytest.raises(WorkflowReadingError, match="exactly one workflow"):
        publisher(documents)


def test_check_mode_is_refused_on_the_publisher() -> None:
    """The publisher names ``mode: upload``; check mode gates, it does not publish."""
    step = upload_step(load_document(PUBLISHER.replace("mode: upload", "mode: check")))
    assert any("mode" in violation for violation in input_violations(step)), (
        "check mode was accepted"
    )


def test_the_retired_checksum_is_found() -> None:
    """The retired input is found wherever it is passed."""
    document = load_document(
        PUBLISHER.replace(
            "mode: upload", "mode: upload\n          installer-checksum: ${{ x }}"
        )
    )
    assert retired_names(document), "installer-checksum was not found"
