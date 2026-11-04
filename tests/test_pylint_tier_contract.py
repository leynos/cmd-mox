"""Contract tests for the PyPy Pylint lint tier.

The second lint tier runs a pinned Pylint as a uv tool on managed PyPy 3.12.
Three properties of that tier are easy to lose without any test noticing: the
interpreter pin, which decides the grammar Pylint parses with; the
`syntax-error` message, which decides whether a module that grammar cannot
parse fails the lint or is skipped without a word; and the
`unknown-option-value` split, which decides whether inline suppression
pragmas are validated at all. The first two regressed silently before: a bare
`pypy` moved to a newer PyPy with no commit here, and a disabled
`syntax-error` let eleven modules go unlinted.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = _REPO_ROOT / "Makefile"
PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"
DF12_RCFILE_PATH = _REPO_ROOT / "pylintrc-df12.toml"

pytestmark = pytest.mark.skipif(
    not (MAKEFILE_PATH.exists() and PYPROJECT_PATH.exists()),
    reason=(
        "Makefile or pyproject.toml not present in this working copy (for "
        "example inside mutmut's mutants/ sandbox)"
    ),
)


def _makefile_variable(name: str) -> str:
    """Return the value assigned to a Makefile variable.

    Parameters
    ----------
    name : str
        The variable name, assigned with ``=`` or ``?=``.

    Returns
    -------
    str
        The assigned value with surrounding whitespace removed.
    """
    text = MAKEFILE_PATH.read_text(encoding="utf-8")
    match = re.search(rf"^{re.escape(name)}\s*\??=\s*(.+)$", text, flags=re.MULTILINE)
    assert match is not None, f"{name} is not defined in the Makefile"
    return match.group(1).strip()


def test_pylint_tier_pins_the_pypy_minor_version() -> None:
    """The tier must name the PyPy minor version whose grammar it parses."""
    assert _makefile_variable("PYLINT_PYTHON") == "pypy@3.12", (
        "PYLINT_PYTHON must pin PyPy to its minor version; a bare `pypy` "
        "follows whatever uv resolves next"
    )


def test_pylint_tier_runs_the_pinned_release_on_managed_python() -> None:
    """The tier must run the pinned Pylint on a uv-managed interpreter."""
    assert re.fullmatch(r"\d+\.\d+\.\d+", _makefile_variable("PYLINT_VERSION")), (
        "PYLINT_VERSION must pin an exact Pylint release"
    )
    command = _makefile_variable("PYLINT")
    for fragment in (
        "tool --managed-python --python $(PYLINT_PYTHON)",
        "--from 'pylint==$(PYLINT_VERSION)' -- pylint",
    ):
        assert fragment in command, f"PYLINT must contain {fragment!r}: {command}"


def test_pylint_reports_unparseable_modules() -> None:
    """The Pylint policy must not disable `syntax-error`."""
    config = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    disabled = config["tool"]["pylint"]["messages control"]["disable"]
    assert "syntax-error" not in disabled, (
        "pyproject.toml must not disable syntax-error: a module PyPy cannot "
        f"parse would then be skipped silently; disable={disabled!r}"
    )


def test_classic_pylint_defers_pragma_validation_to_the_df12_tier() -> None:
    """The two Pylint passes must validate inline pragma names exactly once.

    Inline pragmas may name df12 messages (``trivial-attribute-wrapper``, for
    example), which the classic pass never registers. Pylint reports W0012 for
    such a name even under ``disable = ["all"]``, so the classic pass must
    disable it by name; the df12 pass registers both vocabularies and must
    enable it. If both disables were dropped a df12 pragma would fail lint; if
    both enables were dropped a misspelled pragma would silence nothing and no
    test would notice.
    """
    classic = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    classic_disabled = classic["tool"]["pylint"]["messages control"]["disable"]
    assert "unknown-option-value" in classic_disabled, (
        "pyproject.toml must disable unknown-option-value for the classic "
        "pass: this pass cannot register df12 pragma names; "
        f"disable={classic_disabled!r}"
    )

    df12 = tomllib.loads(DF12_RCFILE_PATH.read_text(encoding="utf-8"))
    df12_control = df12["tool"]["pylint"]["messages control"]
    assert "unknown-option-value" in df12_control["enable"], (
        "pylintrc-df12.toml must enable unknown-option-value: it is the only "
        "pass that can validate a df12 pragma name against the complete "
        f"vocabulary; enable={df12_control['enable']!r}"
    )
    assert "unknown-option-value" not in df12_control.get("disable", []), (
        "pylintrc-df12.toml must not disable unknown-option-value"
    )
