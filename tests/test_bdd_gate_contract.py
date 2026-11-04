"""Contract tests for the behavioural BDD execution gate.

``make bdd`` runs a representative set of pytest-bdd scenarios in the locked
project environment before the full coverage run. The gate exists because a
dependency update can leave every BDD module failing at step injection while
collection still succeeds, so a collection-only smoke check would report
health that the scenarios do not have.

The scenarios themselves are the regression evidence; they are not duplicated
here. What these tests hold is the wiring: the target is pinned in the
Makefile, it runs whole BDD modules rather than collecting them, it uses the
same locked group the rest of the suite uses, and CI actually calls it.
"""

from __future__ import annotations

import re
import shlex
import tomllib
import typing as typ
from pathlib import Path

import pytest
import yaml

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = _REPOSITORY_ROOT / "Makefile"
CI_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT_PATH = _REPOSITORY_ROOT / "pyproject.toml"

pytestmark = pytest.mark.skipif(
    not (MAKEFILE_PATH.exists() and PYPROJECT_PATH.exists()),
    reason=(
        "Makefile or pyproject.toml not present in this working copy (for "
        "example inside mutmut's mutants/ sandbox)"
    ),
)

#: The representative modules the gate must execute. Each one failed with
#: ``AttributeError: 'FixtureManager' object has no attribute
#: '_arg2fixturedefs'`` under pytest 9.2 with pytest-bdd 9.0.0, and each
#: exercises a different step-registration path: scenario-level steps, shared
#: assertion steps, and the plugin's own fixture integration.
_BDD_MODULES: typ.Final[tuple[str, ...]] = (
    "tests/test_command_double_record_bdd.py",
    "tests/test_command_double_replay_bdd.py",
    "tests/test_recording_session_bdd.py",
    "tests/test_replay_session_bdd.py",
    "tests/test_documentation_bdd.py",
    "tests/test_pytest_plugin_bdd.py",
)

#: ``--collect-only`` reports zero executed scenarios and a green exit status
#: for a module whose steps cannot be injected, which is the failure this gate
#: exists to catch.
_COLLECTION_ONLY_FLAGS = frozenset({
    "--collect-only",
    "--co",
    "--setup-plan",
    "--fixtures",
    "--fixtures-per-test",
})


def _makefile_variable(name: str) -> str:
    """Return a Makefile variable's assigned value.

    A multi-line assignment is returned with its backslash continuations
    removed and its pieces joined by a space.

    Parameters
    ----------
    name : str
        The variable name, assigned with ``=`` or ``?=``.

    Returns
    -------
    str
        The joined value with surrounding whitespace removed.
    """
    lines = MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    start = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(rf"{re.escape(name)}\s*\??=", line)
        ),
        None,
    )
    assert start is not None, f"{name} is not defined in the Makefile"
    pieces = [lines[start].split("=", maxsplit=1)[1]]
    index = start
    while lines[index].rstrip().endswith("\\"):
        index += 1
        pieces.append(lines[index])
    return " ".join(piece.rstrip("\\").strip() for piece in pieces).strip()


def _recipe_lines(target: str) -> list[str]:
    """Return the recipe lines of a Makefile target.

    Parameters
    ----------
    target : str
        The target name, without the colon.

    Returns
    -------
    list[str]
        One entry per recipe line, with the leading tab removed.
    """
    lines = MAKEFILE_PATH.read_text(encoding="utf-8").splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if re.fullmatch(rf"{re.escape(target)}:.*", line)
    )
    recipe: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line.removeprefix("\t"))
    return recipe


def _joined_recipe(target: str) -> str:
    """Return a target's recipe as one shell text.

    Line continuations are joined so a command split across lines is read as
    the single invocation Make hands to the shell.

    Returns
    -------
    str
        The joined recipe text.
    """
    return " ".join(
        line.replace("\\\n", " ").removesuffix("\\").strip()
        for line in _recipe_lines(target)
    )


def _target_arguments(target: str) -> tuple[str, ...]:
    """Return the shell tokens of the final command in a target's recipe.

    Returns
    -------
    tuple[str, ...]
        The tokens of the last non-empty recipe line.
    """
    commands = [line for line in _recipe_lines(target) if line.strip()]
    assert commands, f"the {target} target has no recipe"
    joined = commands[-1].replace("\\\n", " ").replace("\\", " ")
    return tuple(shlex.split(joined))


def test_bdd_target_is_pinned_to_representative_modules() -> None:
    """The gate must name whole modules, not a directory or collection mode."""
    declared = tuple(shlex.split(_makefile_variable("BDD_MODULES").replace("\\", " ")))
    assert set(declared) == set(_BDD_MODULES), (
        "BDD_MODULES must name exactly the representative BDD modules; "
        f"found {declared!r}"
    )
    assert all(module.endswith("_bdd.py") for module in declared), (
        "every BDD_MODULES entry must be a whole module; a directory or a "
        "test-name pattern lets an unexecutable module stay in the set"
    )
    tokens = _target_arguments("bdd")
    assert "$(BDD_MODULES)" in _joined_recipe("bdd"), (
        "the bdd target must pass BDD_MODULES to pytest"
    )
    assert tokens[:2] == ("$(UV_GATE)", "run"), (
        f"the bdd target must run through uv_gate, found {tokens!r}"
    )


def test_bdd_target_does_not_collect_only() -> None:
    """A gate that only collects proves nothing about step injection."""
    recipe_tokens = {
        token for line in _recipe_lines("bdd") for token in shlex.split(line)
    }
    assert not (_COLLECTION_ONLY_FLAGS & recipe_tokens), (
        "the bdd target must execute scenarios; a collection-only flag "
        "reports green for a module whose steps cannot be injected"
    )


def test_bdd_target_runs_in_the_locked_project_environment() -> None:
    """The gate must reuse the same locked dev group as ``make test``."""
    recipe = _joined_recipe("bdd")
    assert "$(UV_GATE) run --group dev" in recipe, (
        "the bdd target must run through uv_gate in the locked dev group, so "
        "it executes the versions the lock file selects"
    )
    assert "--frozen" not in recipe, (
        "the bdd target must not override uv_gate's own freshness policy"
    )
    assert "uv sync" not in recipe, (
        "the bdd target must not resolve or refresh dependencies; uv_gate "
        "run already refuses a stale lock"
    )


def test_bdd_target_is_ordered_before_full_coverage_in_ci() -> None:
    """CI must run the BDD gate before coverage can speak for the suite."""
    if not CI_WORKFLOW_PATH.exists():
        pytest.skip("ci.yml is not present in this working copy")
    workflow = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    # Parse rather than search the raw text: a commented-out step must not
    # satisfy the ordering contract.

    document = yaml.safe_load(workflow)
    steps = document["jobs"]["quality"]["steps"]
    names = [step.get("name") for step in steps]
    runs = [step.get("run") for step in steps]
    assert "Run the BDD execution gate" in names, (
        "CI must call `make bdd`; the full suite alone reports a step "
        "injection failure only as one of many failures"
    )
    bdd_index = runs.index("make bdd")
    coverage_index = next(
        index
        for index, step in enumerate(steps)
        if step.get("name") == "Generate coverage"
    )
    assert bdd_index < coverage_index, "the BDD gate must run before coverage"


def test_locked_pytest_bdd_supports_pytest_92() -> None:
    """The lock must hold the pytest-bdd release that fixed the registry read."""
    lock = tomllib.loads((_REPOSITORY_ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = {
        package["name"]: package["version"]
        for package in lock["package"]
        if package["name"] in {"pytest", "pytest-bdd"}
    }
    bdd_version = tuple(int(part) for part in versions["pytest-bdd"].split("."))
    pytest_version = tuple(int(part) for part in versions["pytest"].split("."))
    assert pytest_version >= (9, 2), (
        "this contract documents the pytest 9.2 compatibility floor; it should "
        "be revisited when the pinned pytest moves on"
    )
    assert bdd_version >= (9, 0, 1), (
        "pytest-bdd 9.0.0 reads FixtureManager._arg2fixturedefs, which pytest "
        "9.2 removed; the lock must not select it again"
    )


def test_every_gated_module_executes_scenarios() -> None:
    """Each named module must contain at least one ``@scenario``.

    A path that names no module, or names one whose scenarios were removed,
    would make ``pytest`` exit 4 (no tests ran) rather than fail silently, but
    only if the module is actually passed; this asserts the modules exist and
    carry scenarios so the gate cannot degenerate into a no-op.
    """
    for module in _BDD_MODULES:
        path = _REPOSITORY_ROOT / module
        assert path.is_file(), f"{module} is named by BDD_MODULES but does not exist"
        assert "@scenario(" in path.read_text(encoding="utf-8"), (
            f"{module} is named by BDD_MODULES but declares no @scenario"
        )
