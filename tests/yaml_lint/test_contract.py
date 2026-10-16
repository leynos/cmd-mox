"""Contracts for the YAML lint gate in ``make lint`` and CI.

The judgement functions in ``support`` are driven against fixtures first,
because a rule exercised only over this repository's own compliant files would
pass whether or not it detects anything, and then applied to the real files.
``test_boundary`` runs the Make targets. The Concordat ``yaml-lint-baseline``
rule audits the same wiring with a full Makefile parse.
"""

from __future__ import annotations

import pytest

from .fixtures import CANONICAL_ESTATE_BASE, GOOD, PINNED, code_lines, gate
from .support import (
    GATE_COMMAND,
    HAND_ROLLED,
    INSTALLER,
    PIPE,
    REPO_ROOT,
    ci_installs_before_lint,
    gate_is_wired,
    jobs,
)


@pytest.mark.parametrize(
    ("makefile", "wired"),
    [
        pytest.param(GOOD, True, id="wired"),
        pytest.param(GOOD.replace(" --strict", ""), False, id="not strict"),
        pytest.param(GOOD.replace(" '*.yaml'", ""), False, id="one pathspec"),
        pytest.param(
            GOOD.replace(f"{PIPE} $(YAMLLINT) --strict --", "$(YAMLLINT) --strict ."),
            False,
            id="whole tree",
        ),
        pytest.param(
            GOOD.replace("'*.yaml' |", "'*.yaml' .github/workflows |"),
            False,
            id="workflows only",
        ),
        pytest.param(
            GOOD.replace(
                "lint: yamllint actionlint", "lint:\n\ttrue\nother: yamllint actionlint"
            ),
            False,
            id="outside the lint closure",
        ),
        pytest.param(
            GOOD.replace("\t$(ACTIONLINT)", "\ttrue"), False, id="no actionlint"
        ),
        pytest.param(
            GOOD.replace("--strict --\n", "--strict -- || true\n"),
            False,
            id="yamllint failure ignored",
        ),
        pytest.param(
            GOOD.replace("\tgit ls-files", "\t-git ls-files"),
            False,
            id="yamllint prefixed with a dash",
        ),
        pytest.param(
            GOOD.replace("\tgit ls-files", "\t! git ls-files"),
            False,
            id="yamllint negated",
        ),
        pytest.param(
            GOOD.replace("\t$(ACTIONLINT)", "\t$(ACTIONLINT) || true"),
            False,
            id="actionlint failure ignored",
        ),
        pytest.param(
            GOOD.replace("\t$(ACTIONLINT)", "\t-$(ACTIONLINT)"),
            False,
            id="actionlint prefixed with a dash",
        ),
        pytest.param(
            GOOD.replace("\t$(ACTIONLINT)", "\t! $(ACTIONLINT)"),
            False,
            id="actionlint negated",
        ),
        pytest.param(
            "lint:\n\t$(MAKE) gate\ngate: yamllint actionlint\n"
            + GOOD.split("\n", 1)[1],
            True,
            id="delegated",
        ),
        pytest.param(
            "lint:\n\t$(MAKE) gate\ngate: yamllint actionlint\n"
            + GOOD.split("\n", 1)[1].replace("\t$(ACTIONLINT)", "\t-$(ACTIONLINT)"),
            False,
            id="bypassed through delegation",
        ),
    ],
)
def test_the_gate_judgement_detects_each_defect(makefile: str, *, wired: bool) -> None:
    """Each defect fails the judgement, and the compliant shapes pass it."""
    assert gate_is_wired(makefile) is wired, makefile


@pytest.mark.parametrize(
    ("workflow", "installs"),
    [
        pytest.param(gate(f"- uses: {PINNED}", "- run: make lint"), True, id="pinned"),
        pytest.param(
            gate(f"- uses: {PINNED}", "- run: make lint MDLINT=true"),
            True,
            id="options",
        ),
        pytest.param(
            gate(f"- uses: {PINNED}", "- run: /usr/bin/make lint"), True, id="absolute"
        ),
        pytest.param(
            gate(f"- uses: {INSTALLER}@v1", "- run: make lint"), False, id="unpinned"
        ),
        pytest.param(gate("- run: make lint"), False, id="missing"),
        pytest.param(
            gate("- run: make lint", f"- uses: {PINNED}"), False, id="installed after"
        ),
        pytest.param(
            gate(f"- uses: {PINNED}", "- run: make test"), False, id="no lint"
        ),
        pytest.param(
            gate(f"- uses: {PINNED}", "- run: make lint || true"), False, id="ignored"
        ),
        pytest.param(
            gate(
                f"- uses: {PINNED}",
                "- run: make lint",
                "  continue-on-error: true",
            ),
            False,
            id="continue on error",
        ),
        pytest.param(
            gate(
                "- run: make lint",
                extra_job=f"  tools:\n    steps:\n      - uses: {PINNED}\n",
            ),
            False,
            id="installer in another job",
        ),
    ],
)
def test_the_ci_judgement_detects_each_defect(workflow: str, *, installs: bool) -> None:
    """Each defect fails the judgement, and the compliant shapes pass it."""
    assert ci_installs_before_lint(workflow) is installs, workflow


def test_make_lint_runs_the_yaml_lint_gate() -> None:
    """The real Makefile wires the gate into ``lint``."""
    assert gate_is_wired((REPO_ROOT / "Makefile").read_text(encoding="utf-8")), (
        "the lint closure lacks the YAML lint gate"
    )


def test_ci_installs_the_tools_before_it_runs_the_gate() -> None:
    """Every real workflow that runs the gate installs the tools, pinned."""
    workflows = sorted((REPO_ROOT / ".github/workflows").glob("*.y*ml"))
    texts = [path.read_text(encoding="utf-8") for path in workflows]
    gated = [
        text
        for text in texts
        if any(GATE_COMMAND.match(x) for j in jobs(text) for x in j)
    ]
    assert gated, "no workflow runs the lint gate"
    assert all(ci_installs_before_lint(text) for text in gated), (
        "a lint job lacks the pinned installer before the gate"
    )
    assert not any(HAND_ROLLED.search(text) for text in texts), (
        "a workflow still installs a lint tool by hand"
    )


def test_the_local_yamllint_configuration_extends_the_estate_base() -> None:
    """The local file sets nothing but ``extends``."""
    local = (REPO_ROOT / ".yamllint.yml").read_text(encoding="utf-8")
    assert code_lines(local)[-1] == "extends: .yamllint-estate.yml", local
    first = next(line for line in code_lines(local) if line != "---")
    assert first == "extends: .yamllint-estate.yml", local


def test_actionlint_accepts_the_estate_runner_labels() -> None:
    """The ubicloud runner labels are declared."""
    config = (REPO_ROOT / ".github/actionlint.yaml").read_text(encoding="utf-8")
    assert "- ubicloud-*" in code_lines(config), config


def test_the_estate_base_is_the_canonical_configuration() -> None:
    """Every rule, heading and value of the estate base is the estate's.

    The lines are compared in order with their indentation, so a dropped or
    changed setting fails even when its text survives under a sibling rule.
    The Concordat ``yaml-lint-baseline`` rule audits the file by digest.
    """
    base = (REPO_ROOT / ".yamllint-estate.yml").read_text(encoding="utf-8")
    lines = [line.split(" #")[0].rstrip() for line in base.splitlines()]
    code = [
        line for line in lines if line.strip() and not line.lstrip().startswith("#")
    ]
    assert code == CANONICAL_ESTATE_BASE, (
        "the estate base differs from the canonical one"
    )
