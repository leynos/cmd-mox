"""Contract tests for what the mutmut sandbox must provide.

Mutmut copies ``source_paths``, ``also_copy``, and a fixed set of test
directories into ``mutants/`` before it runs the suite there. A test that
reads a repository file outside that set, or needs a tool the sandbox does
not install, fails the mutation baseline even though the source is healthy.

These tests pin the remedies for the two gaps found by running the suite in
a faithful sandbox: the committed spelling policy must be copied, and the
Makefile-parser contract must skip when its tool is unavailable without
hiding the contracts that do not need it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKYLOS_CONTRACT = ROOT / "tests" / "test_skylos_lint_contract.py"
PARSER_CONTRACTS = (
    "test_lint_recipe_runs_the_production_dead_code_gate"
    " or test_whitelist_target_uses_skylos_subcommand_contract"
)
INDEPENDENT_CONTRACTS = (
    "test_skylos_whitelist_is_reviewed_and_reasoned"
    " or test_skylos_gate_is_strict"
    " or test_ci_runs_the_lint_target_and_installs_makeutil"
)


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


def _inherited_environment() -> dict[str, str]:
    """Return the current environment without pytest add-ons."""
    environment = os.environ.copy()
    environment.pop("PYTEST_ADDOPTS", None)
    return environment


@pytest.fixture(name="without_makeutil")
def without_makeutil_fixture(tmp_path: Path) -> dict[str, str]:
    """Return an environment whose ``PATH`` contains no ``makeutil``."""
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    environment = _inherited_environment()
    environment["PATH"] = str(empty_bin)
    return environment


def _run_contract(
    environment: dict[str, str], *selection: str
) -> subprocess.CompletedProcess[str]:
    """Run the Skylos contract module with *selection* and return the result."""
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [
            sys.executable,
            "-m",
            "pytest",
            str(SKYLOS_CONTRACT),
            *selection,
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


def test_the_makefile_parser_contract_skips_without_makeutil(
    without_makeutil: dict[str, str],
) -> None:
    """The sandbox does not install makeutil, so the parser test must skip.

    Reproduces the baseline failure from the mutation workflow: the contract
    called ``makeutil`` and raised ``FileNotFoundError`` instead of reporting
    that the tool was unavailable.
    """
    result = _run_contract(
        without_makeutil,
        "-k",
        "test_lint_recipe_runs_the_production_dead_code_gate",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 skipped" in result.stdout, result.stdout


def test_contracts_without_the_parser_still_run_without_makeutil(
    without_makeutil: dict[str, str],
) -> None:
    """The skip must not hide contracts that never read the Makefile."""
    result = _run_contract(without_makeutil, "-k", INDEPENDENT_CONTRACTS)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout, result.stdout


@pytest.mark.skipif(
    shutil.which("makeutil") is None,
    reason="makeutil is not installed in this working copy",
)
def test_the_parser_contracts_run_when_makeutil_is_available() -> None:
    """An unconditional skip would pass the missing-parser test vacuously."""
    result = _run_contract(_inherited_environment(), "-k", PARSER_CONTRACTS)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout, result.stdout
