"""Property tests for the YAML lint gate judgements.

``test_contract`` enumerates each defect class by hand. These properties
generalise the same judgements over delegation depth, installer ordering, and
unrelated jobs, where a hand-written case for every permutation would be
unreadable and easy to leave incomplete.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from .fixtures import PINNED, gate
from .support import PIPE, ci_installs_before_lint, gate_is_wired

YAMLLINT_RECIPE = f"set -o pipefail && {PIPE} $(YAMLLINT) --strict --"
ACTIONLINT_RECIPE = "$(ACTIONLINT)"
# Make recipe prefixes and shell fallbacks that stop a failure failing the job.
PREFIXES = ("", "-", "@-", "! ")
SUFFIXES = ("", " || true", " || :", " || exit 0", ";")


def lint_targets(depth: int) -> list[str]:
    """Return the rules reaching both tools through ``depth`` prerequisite hops.

    Parameters
    ----------
    depth : int
        The number of prerequisite hops between ``lint`` and the tools.

    Returns
    -------
    list[str]
        One rule line per hop, ending at the rule that runs both tools.
    """
    if depth == 0:
        return ["lint: yamllint actionlint"]
    return [
        "lint: gate1",
        *(f"gate{level}: gate{level + 1}" for level in range(1, depth)),
        f"gate{depth}: yamllint actionlint",
    ]


def makefile(depth: int, bypass: tuple[str, str, str] | None = None) -> str:
    """Build a gate reached in ``depth`` hops, optionally bypassing one tool.

    Parameters
    ----------
    depth : int
        The number of prerequisite hops between ``lint`` and the tools.
    bypass : tuple[str, str, str] | None
        ``(tool, prefix, suffix)`` wrapping one tool's recipe, or ``None`` for
        a sound makefile.

    Returns
    -------
    str
        The Makefile text.
    """
    yamllint, actionlint = YAMLLINT_RECIPE, ACTIONLINT_RECIPE
    if bypass is not None:
        tool, prefix, suffix = bypass
        if tool == "yamllint":
            yamllint = f"{prefix}{yamllint}{suffix}"
        else:
            actionlint = f"{prefix}{actionlint}{suffix}"
    rules = [
        *lint_targets(depth),
        f"yamllint:\n\t{yamllint}",
        f"actionlint:\n\t{actionlint}",
    ]
    return "\n".join(rules) + "\n"


def workflow(*, installs_first: bool, suffix: str, others: int) -> str:
    """Build a gate job among ``others`` unrelated jobs.

    Parameters
    ----------
    installs_first : bool
        Place the pinned installer before the gate step.
    suffix : str
        Text appended to the gate command.
    others : int
        Number of unrelated jobs to place before the gate job.

    Returns
    -------
    str
        The workflow text.
    """
    install = f"- uses: {PINNED}"
    gate_step = f"- run: make lint{suffix}"
    steps = (install, gate_step) if installs_first else (gate_step, install)
    extra = "".join(
        f"  extra{index}:\n    steps:\n      - run: make test\n"
        for index in range(others)
    )
    return gate(*steps, extra_job=extra)


@given(depth=st.integers(min_value=0, max_value=8))
def test_delegation_depth_never_hides_the_gate(depth: int) -> None:
    """A sound gate stays wired however many prerequisite hops ``lint`` needs."""
    assert gate_is_wired(makefile(depth)), depth


@given(
    depth=st.integers(min_value=0, max_value=8),
    tool=st.sampled_from(("yamllint", "actionlint")),
    prefix=st.sampled_from(PREFIXES),
    suffix=st.sampled_from(SUFFIXES),
)
def test_no_bypass_shape_can_wire_the_gate(
    depth: int, tool: str, prefix: str, suffix: str
) -> None:
    """A bypass on either tool leaves the gate unwired at any depth."""
    text = makefile(depth, (tool, prefix, suffix))
    assert gate_is_wired(text) is (not prefix and not suffix), text


@given(
    installs_first=st.booleans(),
    suffix=st.sampled_from(SUFFIXES),
    others=st.integers(min_value=0, max_value=5),
)
def test_ci_order_and_bypass_survive_unrelated_jobs(
    *, installs_first: bool, suffix: str, others: int
) -> None:
    """Unrelated jobs never mask install order or a bypassed gate step."""
    text = workflow(installs_first=installs_first, suffix=suffix, others=others)
    assert ci_installs_before_lint(text) is (installs_first and not suffix), text
