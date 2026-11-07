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
BEHAVIOURAL_CONTRACTS = (
    "test_skylos_allow_rejects_missing_or_whitespace_values"
    " or test_skylos_allow_forwards_generated_argument_boundaries"
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


#: Tools the whitelist target invokes; the recorder it runs uses an absolute
#: interpreter path, and make supplies its own ``/bin/sh``.
_REQUIRED_TOOLS = ("env", "flock", "make", "sh")


def _install_isolated_tools(directory: Path) -> None:
    """Create *directory* holding only the tools the behavioural contracts need.

    Symlinking each required tool into one otherwise empty directory keeps
    ``makeutil`` unreachable whatever the host's layout: dropping "the
    directory that contains makeutil" fails when another PATH entry is an
    alias of that directory, such as ``/bin`` pointing at ``/usr/bin``.
    Callers build the ``PATH`` value from *directory* themselves, so the write
    stays visible at the call site.
    """
    directory.mkdir()
    for name in _REQUIRED_TOOLS:
        source = shutil.which(name)
        assert source, f"{name} must be on the path"
        (directory / name).symlink_to(source)


def test_the_isolated_path_never_inherits_a_neighbouring_makeutil(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``make`` and ``makeutil`` may share a directory, and ``/bin`` may alias it."""
    tools = tmp_path / "tools"
    tools.mkdir()
    for name in (*_REQUIRED_TOOLS, "makeutil"):
        executable = tools / name
        executable.write_text("#!/bin/sh\n", encoding="utf-8")
        executable.chmod(0o755)
    alias = tmp_path / "bin"
    alias.symlink_to(tools, target_is_directory=True)

    real_which = shutil.which

    def fake_which(name: str, path: str | None = None) -> str | None:
        """Resolve ambient lookups through the aliased tools directory."""
        if path is not None:
            return real_which(name, path=path)
        return real_which(name, path=os.pathsep.join((str(tools), str(alias))))

    monkeypatch.setattr(shutil, "which", fake_which)
    isolated_directory = tmp_path / "isolated"
    _install_isolated_tools(isolated_directory)
    isolated = str(isolated_directory)

    assert {entry.name for entry in Path(isolated).iterdir()} == set(_REQUIRED_TOOLS), (
        "the isolated PATH must hold only the required tools"
    )
    assert real_which("makeutil", path=isolated) is None, (
        "makeutil must stay unreachable through the isolated PATH"
    )
    for name in _REQUIRED_TOOLS:
        assert real_which(name, path=isolated) is not None, (
            f"{name} must stay reachable through the isolated PATH"
        )


@pytest.mark.skipif(
    shutil.which("flock") is None,
    reason="flock(1) is not installed on this host",
)
def test_behavioural_contracts_run_without_the_parser(tmp_path: Path) -> None:
    """The sandbox must keep running the whitelist behaviour checks."""
    tools = tmp_path / "bin"
    _install_isolated_tools(tools)
    environment = _inherited_environment()
    environment["PATH"] = str(tools)

    assert shutil.which("makeutil", path=environment["PATH"]) is None, (
        "the behavioural selection must run with no parser on PATH"
    )
    result = _run_contract(environment, "-k", BEHAVIOURAL_CONTRACTS)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout, result.stdout


@pytest.mark.skipif(
    shutil.which("makeutil") is None,
    reason="makeutil is not installed in this working copy",
)
def test_the_parser_contracts_run_when_makeutil_is_available() -> None:
    """An unconditional skip would pass the missing-parser test vacuously."""
    result = _run_contract(_inherited_environment(), "-k", PARSER_CONTRACTS)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "2 passed" in result.stdout, result.stdout
