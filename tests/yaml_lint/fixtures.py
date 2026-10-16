"""Fixture builders and the canonical estate base for the YAML lint contract."""

from __future__ import annotations

from .support import INSTALLER, PIPE


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
