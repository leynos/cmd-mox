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
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
MAKEFILE_PATH = _REPO_ROOT / "Makefile"
CI_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
GATE_SCRIPT_PATH = _REPO_ROOT / "scripts" / "duplication_gate.py"

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
        ("ty", r"ty@\$\(TY_VERSION\)"),
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


def _ci_env_pin(name: str) -> str:
    """Extract a job-level ``env:`` value quoted in the CI workflow.

    Parameters
    ----------
    name : str
        Environment variable name defined under the job's ``env:`` mapping.

    Returns
    -------
    str
        The single-quoted value assigned to *name*.
    """
    text = CI_WORKFLOW_PATH.read_text(encoding="utf-8")
    matches = re.findall(rf"^\s+{re.escape(name)}:\s*'([^']+)'\s*$", text, re.MULTILINE)
    assert matches, f"ci.yml does not define {name} as a quoted env value"
    assert len(matches) == 1, f"ci.yml defines {name} more than once: {matches}"
    return matches[0]


def _pyproject_nose_pin() -> str:
    """Extract ``[tool.nose].version`` from pyproject.toml."""  # ruff: ignore[docstring-missing-returns] - private reader has an obvious version-string return
    text = (CI_WORKFLOW_PATH.parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    nose_table = re.search(
        r"^\[tool\.nose\]\n(.*?)(?=^\[)", text, re.MULTILINE | re.DOTALL
    )
    assert nose_table is not None, "pyproject.toml has no [tool.nose] table"
    match = re.search(r'^version\s*=\s*"([^"]+)"', nose_table.group(1), re.MULTILINE)
    assert match is not None, "[tool.nose] does not pin a version"
    return match.group(1)


def test_nose_pin_agrees_across_makefile_ci_and_pyproject() -> None:
    """The detector version must be pinned identically in all three places.

    ``make install-nose`` installs ``NOSE_VERSION``, the gate instructs the
    detector to report ``[tool.nose].version``, and CI caches on its own copy.
    Any drift produces a cache that can never hit, or an install the gate
    then rejects at run time.
    """
    makefile_version = _makefile_pin("nose")
    ci_version = _ci_env_pin("NOSE_VERSION")
    pyproject_version = _pyproject_nose_pin()

    for label, value in (
        ("Makefile NOSE_VERSION", makefile_version),
        ("ci.yml NOSE_VERSION", ci_version),
        ("pyproject.toml [tool.nose].version", pyproject_version),
    ):
        assert VERSION_RE.fullmatch(value), f"{label} is not a version: {value!r}"

    assert makefile_version == ci_version == pyproject_version, (
        "nose version drift: Makefile pins "
        f"{makefile_version}, ci.yml pins {ci_version}, pyproject.toml pins "
        f"{pyproject_version}"
    )


def test_typecheck_splits_the_tooling_pass_from_the_application_pass() -> None:
    """``make typecheck`` must check ``scripts/`` on its own interpreter.

    The application floor is Python 3.12 while the PEP 723 scripts under
    ``scripts/`` declare 3.14, so one invocation cannot check both. The
    application pass excludes ``scripts`` and a second pass points ty at
    ``scripts/ty.toml``, which carries that tree's own interpreter and settings.
    """
    text = MAKEFILE_PATH.read_text(encoding="utf-8")
    assert "--exclude scripts" in text, (
        "the application typecheck still swallows the PEP 723 tooling tree"
    )
    assert "--config-file scripts/ty.toml" in text, (
        "no second typecheck pass targets the scripts tree"
    )
    tooling_config = _REPO_ROOT / "scripts" / "ty.toml"
    assert tooling_config.is_file(), "scripts/ty.toml is missing"
    settings = tooling_config.read_text(encoding="utf-8")
    assert 'python-version = "3.14"' in settings, (
        "scripts/ty.toml must set the tooling interpreter"
    )
    assert "allowed-unresolved-imports" in settings, (
        "scripts/ty.toml must declare the PEP 723 dependencies ty cannot resolve"
    )


def _quality_job_steps() -> list[dict[str, object]]:
    """Return the ``quality`` job's steps from the decoded CI workflow.

    Returns
    -------
    list[dict[str, object]]
        The job's step mappings, in workflow order.
    """
    workflow = yaml.safe_load(CI_WORKFLOW_PATH.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict), "ci.yml must decode to a mapping"
    jobs = workflow.get("jobs")
    assert isinstance(jobs, dict), "ci.yml must declare a jobs mapping"
    quality = jobs.get("quality")
    assert isinstance(quality, dict), "ci.yml must declare a quality job"
    steps = quality.get("steps")
    assert isinstance(steps, list), "the quality job must declare a list of steps"
    return [step for step in steps if isinstance(step, dict)]


def test_duplication_gate_pins_match_the_script_metadata() -> None:
    """The Makefile's tool environment must match the gate's PEP 723 block.

    ``scripts/duplication_gate.py`` still states its requirements inline, so
    that `uv run scripts/duplication_gate.py` works when invoked directly.
    The Makefile builds the same environment explicitly for `uv_gate tool`,
    because that channel refuses an unpinned spec and can warm a cold cache.
    Two authored copies of one pin set drift unless something compares them.
    """
    script = GATE_SCRIPT_PATH.read_text(encoding="utf-8")
    block = re.search(
        r"^# /// script\n(.*?)^# ///\s*$", script, re.MULTILINE | re.DOTALL
    )
    assert block is not None, "the gate script has no PEP 723 metadata block"
    metadata = block.group(1)

    floor = re.search(r'^# requires-python = ">=(\d+\.\d+)"', metadata, re.MULTILINE)
    assert floor is not None, "the gate script declares no Python floor"
    makefile = MAKEFILE_PATH.read_text(encoding="utf-8")
    python_pin = re.search(
        r"^DUPLICATION_PYTHON\s*\??=\s*(\S+)\s*$", makefile, flags=re.MULTILINE
    )
    assert python_pin is not None, "the Makefile does not pin DUPLICATION_PYTHON"
    assert python_pin.group(1) == floor.group(1), (
        "DUPLICATION_PYTHON must match the gate script's requires-python floor"
    )

    pins = dict(re.findall(r'"([a-zA-Z0-9_-]+)==([^"]+)"', metadata))
    assert pins, "the gate script declares no pinned dependencies"
    for package, version in pins.items():
        assert _makefile_pin(package) == version, (
            f"{package.upper()}_VERSION must match the gate script's "
            f"pinned {package}=={version}"
        )


def test_ci_caches_and_verifies_the_pinned_detector() -> None:
    """CI must cache the detector by version and re-verify it after restore.

    A cached binary from a different version or platform would otherwise let
    the lint gate run a detector the configuration does not pin.
    """
    steps = _quality_job_steps()

    cache_steps = [
        step for step in steps if str(step.get("uses", "")).startswith("actions/cache")
    ]
    assert len(cache_steps) == 1, "the quality job must restore caches exactly once"
    cache_config = cache_steps[0].get("with")
    assert isinstance(cache_config, dict), "the cache step must pass a `with` mapping"
    assert ".tools/nose" in str(cache_config.get("path", "")), (
        "ci.yml does not cache the repository-local detector directory"
    )
    key = str(cache_config.get("key", ""))
    assert "runner.os" in key, "the detector cache key must include the runner OS"
    assert "env.NOSE_VERSION" in key, (
        "the detector cache key must include the pinned detector version"
    )

    assert any("make install-nose" in str(step.get("run", "")) for step in steps), (
        "ci.yml never runs make install-nose, so a cache miss would leave no detector"
    )
    assert any("make duplication-test" in str(step.get("run", "")) for step in steps), (
        "ci.yml does not run the duplication-gate helper tests"
    )
    assert any(
        ".tools/nose/nose --version" in str(step.get("run", ""))
        and "NOSE_VERSION" in str(step.get("run", ""))
        for step in steps
    ), "ci.yml must re-verify the restored detector against the pinned version"
    assert "--disable-strategies compile,quick-install" in MAKEFILE_PATH.read_text(
        encoding="utf-8"
    ), "make install-nose must forbid cargo-binstall's compile strategies"
