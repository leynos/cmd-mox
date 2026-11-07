"""Contract tests for what the mutmut sandbox must provide.

Mutmut copies ``source_paths``, ``also_copy``, and a fixed set of test
directories into ``mutants/`` before it runs the suite there. A test that
reads a repository file outside that set, or needs a tool the sandbox does
not install, fails the mutation baseline even though the source is healthy.

These tests pin the remedies for the two gaps found by running the suite in
a faithful sandbox: the committed spelling policy must be copied, and the
Makefile-parser contract must skip when its tool is unavailable.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKYLOS_CONTRACT = ROOT / "tests" / "test_skylos_lint_contract.py"


def _also_copy() -> list[str]:
    """Return the paths ``[tool.mutmut]`` copies into the sandbox."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["mutmut"]["also_copy"]


def test_the_spelling_policy_is_copied_into_the_sandbox() -> None:
    """The typos rollout test reads the committed local policy from the root.

    Mutation runs from ``mutants/``, so an uncopied policy file turns that
    test into a ``FileNotFoundError`` in every mutation baseline.
    """
    assert "typos.local.toml" in _also_copy(), (
        "the mutation sandbox must copy the committed spelling policy"
    )


def test_the_makefile_parser_contract_skips_without_makeutil(
    tmp_path: Path,
) -> None:
    """The sandbox does not install makeutil, so the contract must skip.

    Reproduces the baseline failure from the mutation workflow: the contract
    called ``makeutil`` and raised ``FileNotFoundError`` instead of reporting
    that the tool was unavailable.
    """
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    environment = os.environ.copy()
    environment["PATH"] = str(empty_bin)
    environment.pop("PYTEST_ADDOPTS", None)

    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [
            sys.executable,
            "-m",
            "pytest",
            str(SKYLOS_CONTRACT),
            "-k",
            "test_lint_recipe_runs_the_production_dead_code_gate",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=ROOT,
        env=environment,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout, result.stdout
