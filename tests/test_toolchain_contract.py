"""Contract tests keeping Makefile and CI toolchain pins in sync.

The Makefile pins the ruff and ty versions used by the lint, format, and
typecheck gates, and the CI workflow installs the same tools with
``uv tool install``. These tests assert that both places pin each tool to
the same version without asserting any specific version: bumping a pin is
a routine change, but letting the two definitions drift silently produces
gates that pass locally and fail in CI (or the reverse).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = _REPO_ROOT / "Makefile"
CI_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = pytest.mark.skipif(
    not (MAKEFILE_PATH.exists() and CI_WORKFLOW_PATH.exists()),
    reason=(
        "Makefile or CI workflow not present in this working copy (for "
        "example inside mutmut's mutants/ sandbox, which does not copy "
        "the repository root or .github/)"
    ),
)

#: PEP 440-flavoured shape check so an accidentally emptied pin fails
#: loudly rather than comparing two empty strings as equal.
VERSION_RE = re.compile(r"\d+(?:\.\d+)+(?:[a-zA-Z0-9.+-]*)")


def _makefile_pin(tool: str) -> str:
    """Extract a tool's pinned version from the Makefile.

    Parameters
    ----------
    tool : str
        Tool name as used in the ``<TOOL>_VERSION`` Makefile variable.

    Returns
    -------
    str
        The version string assigned to ``<TOOL>_VERSION``.
    """
    variable = f"{tool.upper()}_VERSION"
    text = MAKEFILE_PATH.read_text(encoding="utf-8")
    match = re.search(
        rf"^{re.escape(variable)}\s*\??=\s*(\S+)\s*$", text, flags=re.MULTILINE
    )
    assert match is not None, f"{variable} is not defined in the Makefile"
    return match.group(1)


def _ci_pin(tool: str) -> str:
    """Extract a tool's pinned version from the CI workflow.

    Parameters
    ----------
    tool : str
        Tool name as installed by ``uv tool install`` in ci.yml.

    Returns
    -------
    str
        The version string pinned with ``==`` in the install command.
    """
    text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    matches = re.findall(
        rf"uv tool install '{re.escape(tool)}==([^']+)'",
        text,
    )
    assert matches, f"ci.yml does not pin {tool} via uv tool install '{tool}==...'"
    assert len(matches) == 1, f"ci.yml pins {tool} more than once: {matches}"
    return matches[0]


@pytest.mark.parametrize("tool", ["ruff", "ty"])
def test_makefile_and_ci_pin_the_same_version(tool: str) -> None:
    """The Makefile and ci.yml must pin each tool to the same version."""
    makefile_version = _makefile_pin(tool)
    ci_version = _ci_pin(tool)
    assert VERSION_RE.fullmatch(makefile_version), (
        f"Makefile {tool.upper()}_VERSION does not look like a version: "
        f"{makefile_version!r}"
    )
    assert makefile_version == ci_version, (
        f"{tool} version drift: Makefile pins {makefile_version} but "
        f"ci.yml installs {ci_version}"
    )


@pytest.mark.parametrize(
    ("tool", "usage_re"),
    [
        ("ruff", r"ruff@\$\(RUFF_VERSION\)"),
        ("ty", r"ty==\$\(TY_VERSION\)"),
    ],
)
def test_makefile_commands_use_the_pinned_version(tool: str, usage_re: str) -> None:
    """The Makefile's tool invocations must reference the version variable.

    A pin that exists but is not referenced by the corresponding command
    would silently run whatever version uv resolves.
    """
    text = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert re.search(usage_re, text), (
        f"the Makefile defines {tool.upper()}_VERSION but its {tool} "
        f"command does not reference it (expected pattern {usage_re})"
    )
