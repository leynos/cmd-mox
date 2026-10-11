"""Judgement functions for the YAML lint gate contract tests.

``make lint`` must run ``yamllint --strict`` over the Git-tracked YAML and
actionlint, and CI must install both through the pinned shared installer
before it runs the gate. The judgement functions are driven against fixtures
first, because a rule exercised only over this repository's own compliant
files would pass whether or not it detects anything, and then applied to the
real files. Everything is read as text, so the contract needs no parser
dependency. The Concordat ``yaml-lint-baseline`` rule audits the same wiring
with a full Makefile parse, and ``test_yaml_lint_boundary.py`` runs the Make
targets.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = "leynos/shared-actions/.github/actions/install-yaml-lint"
SHA_PIN = re.compile(r"^[0-9a-f]{40}$")
HAND_ROLLED = re.compile(
    r"uv tool install[^\n]*yamllint|download-actionlint"
    r"|releases/download[^\n]*actionlint"
)
PIPE = "git ls-files -z '*.yml' '*.yaml' | xargs -0 -r"
GATE_COMMAND = re.compile(r"^(- )?run: (/usr/bin/)?make( \S+)* lint( \S+)*$")


def code_lines(text: str) -> list[str]:
    """Drop comments and blank lines, trimming each line.

    Parameters
    ----------
    text : str
        The file text.

    Returns
    -------
    list[str]
        The remaining lines, stripped.
    """
    lines = (line.split(" #")[0].strip() for line in text.splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def opens_a_rule(line: str) -> bool:
    """Say whether a Makefile line opens a rule rather than assigning a variable.

    Parameters
    ----------
    line : str
        One non-recipe Makefile line.

    Returns
    -------
    bool
        ``True`` for ``targets: prerequisites``, ``False`` for comments and
        ``:=`` or ``?=`` assignments.
    """
    if line.startswith("#"):
        return False
    return ":" in line and not any(operator in line for operator in (":=", "?="))


def parse_rules(makefile: str) -> list[tuple[list[str], list[str], list[str]]]:
    """Read the rules of a Makefile without expanding anything.

    Parameters
    ----------
    makefile : str
        The Makefile text.

    Returns
    -------
    list[tuple[list[str], list[str], list[str]]]
        One ``(targets, prerequisites, recipe lines)`` triple per rule.
    """
    rules: list[tuple[list[str], list[str], list[str]]] = []
    for line in makefile.splitlines():
        if line.startswith("\t"):
            if rules:
                rules[-1][2].append(line.strip())
        elif opens_a_rule(line):
            targets, _, rest = line.partition(":")
            prerequisites = rest.split("##")[0].split()
            rules.append((targets.split(), prerequisites, []))
    return rules


def lint_closure(makefile: str) -> list[str]:
    """Collect the recipe lines reachable from ``lint``.

    Parameters
    ----------
    makefile : str
        The Makefile text.

    Returns
    -------
    list[str]
        Every recipe line reachable through prerequisites and literal
        ``$(MAKE) target`` calls.
    """
    rules = parse_rules(makefile)
    pending, seen, lines = ["lint"], set[str](), list[str]()
    while pending:
        target = pending.pop()
        if target in seen:
            continue
        seen.add(target)
        for targets, prerequisites, recipe in rules:
            if target in targets:
                pending.extend(prerequisites)
                pending.extend(
                    line.removeprefix("$(MAKE) ")
                    for line in recipe
                    if line.startswith("$(MAKE) ")
                )
                lines.extend(recipe)
    return lines


def is_bypassed(line: str) -> bool:
    """Say whether a line leaves its tool's failure unable to fail the job.

    Parameters
    ----------
    line : str
        One recipe or step line.

    Returns
    -------
    bool
        ``True`` for a leading ``-`` or ``!``, ``|| true``, ``|| :``, or a
        command followed by ``;``.
    """
    return bool(
        line.startswith(("-", "!", "@-")) or re.search(r"\|\|\s*(true|:)\b|;", line)
    )


def is_sound_yamllint(line: str) -> bool:
    """Say whether a recipe line lints the tracked YAML strictly from Git's list.

    Parameters
    ----------
    line : str
        One recipe line.

    Returns
    -------
    bool
        ``True`` when the line pipes ``git ls-files`` over both extensions into
        a strict ``$(YAMLLINT)`` and its failure is not bypassed.
    """
    return all([
        line.startswith("git ls-files"),
        "'*.yml'" in line,
        "'*.yaml'" in line,
        "| xargs" in line,
        "$(YAMLLINT)" in line,
        "--strict" in line,
        ".github" not in line,
        not is_bypassed(line),
    ])


def gate_is_wired(makefile: str) -> bool:
    """Say whether the lint closure runs strict yamllint over Git's list and actionlint.

    Parameters
    ----------
    makefile : str
        The Makefile text.

    Returns
    -------
    bool
        ``True`` when both tools run in the lint closure and neither failure
        is bypassed.
    """
    lines = lint_closure(makefile)
    actionlint = any(
        line.startswith("$(ACTIONLINT)") and not is_bypassed(line) for line in lines
    )
    return actionlint and any(is_sound_yamllint(line) for line in lines)


def is_job_header(line: str) -> bool:
    """Say whether a workflow line is a job key: indented by exactly two spaces.

    Parameters
    ----------
    line : str
        One raw workflow line.

    Returns
    -------
    bool
        ``True`` for a two-space-indented line.
    """
    return line.startswith("  ") and not line.startswith("   ")


def jobs(workflow: str) -> list[list[str]]:
    """Split a workflow into its jobs, each as its code lines.

    Parameters
    ----------
    workflow : str
        The workflow text.

    Returns
    -------
    list[list[str]]
        One list of stripped code lines per job. A job starts at a key
        indented by exactly two spaces under ``jobs:``.
    """
    found: list[list[str]] = []
    in_jobs = False
    for raw in workflow.splitlines():
        code = raw.split(" #")[0]
        if not code.strip() or code.lstrip().startswith("#"):
            continue
        if not code.startswith(" "):
            in_jobs = code.rstrip() == "jobs:"
        elif in_jobs and is_job_header(code):
            found.append([])
        elif in_jobs and found:
            found[-1].append(code.strip())
    return found


def pins_the_installer(line: str) -> bool:
    """Say whether a step line pins the shared installer to a full commit SHA.

    Parameters
    ----------
    line : str
        One stripped step line.

    Returns
    -------
    bool
        ``True`` for ``uses: <installer>@<40 hex digits>``.
    """
    step = line.removeprefix("- ")
    if not step.startswith("uses: "):
        return False
    reference = step.removeprefix("uses: ").removeprefix(INSTALLER + "@")
    return step.startswith(f"uses: {INSTALLER}@") and bool(SHA_PIN.match(reference))


def ci_installs_before_lint(workflow: str) -> bool:
    """Say whether each job running ``make lint`` installs the tools first.

    Parameters
    ----------
    workflow : str
        The workflow text.

    Returns
    -------
    bool
        ``True`` when some job runs ``make lint`` and every such job pins the
        installer earlier in the same job without ignoring any failure.
    """
    gated = [
        job
        for job in jobs(workflow)
        if any("make" in line and re.search(r"\blint\b", line) for line in job)
    ]

    def sound(job: list[str]) -> bool:
        install = next((i for i, x in enumerate(job) if pins_the_installer(x)), None)
        gate = next((i for i, x in enumerate(job) if GATE_COMMAND.match(x)), None)
        ignored = any(
            "continue-on-error: true" in line
            or is_bypassed(line.removeprefix("- run: "))
            for line in job
            if line.startswith(("- run:", "run:", "continue-on-error"))
        )
        return (
            install is not None and gate is not None and install < gate and not ignored
        )

    return bool(gated) and all(sound(job) for job in gated)


GOOD = (
    "lint: yamllint actionlint\n"
    "yamllint:\n"
    f"\t{PIPE} $(YAMLLINT) --strict --\n"
    "actionlint:\n"
    "\t$(ACTIONLINT)\n"
)
PINNED = f"{INSTALLER}@0123456789abcdef0123456789abcdef01234567"


def gate(*steps: str, extra_job: str = "") -> str:
    """Build a one-job workflow, optionally preceded by another job.

    Parameters
    ----------
    *steps : str
        Step lines, such as ``"- run: make lint"``.
    extra_job : str
        Text of a job placed before the gate job, indented as a job.

    Returns
    -------
    str
        The workflow text.
    """
    body = "".join(f"      {step}\n" for step in steps)
    return f"jobs:\n{extra_job}  gate:\n    steps:\n{body}"


CANONICAL_ESTATE_BASE = [
    "---",
    "extends: default",
    "ignore: |",
    "  **/charts/**/templates/",
    "rules:",
    "  document-start: disable",
    "  comments:",
    "    min-spaces-from-content: 1",
    "  indentation:",
    "    spaces: consistent",
    "    indent-sequences: consistent",
    "    check-multi-line-strings: false",
    "  braces:",
    "    min-spaces-inside: 0",
    "    max-spaces-inside: 1",
    "  brackets:",
    "    min-spaces-inside: 0",
    "    max-spaces-inside: 1",
    "  line-length:",
    "    max: 120",
    "    allow-non-breakable-words: true",
    "    allow-non-breakable-inline-mappings: true",
    "    ignore: |",
    "      .github/workflows/",
    "      .github/actions/",
    "  truthy:",
    "    allowed-values: ['true', 'false']",
    "    check-keys: false",
]
