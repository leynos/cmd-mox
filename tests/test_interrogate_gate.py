"""Contract tests for the Python docstring-coverage gate in the Makefile.

`make lint` must reach an `interrogate` run that covers every Python file the
repository owns, at a threshold of 100, with a guard that fails on any `MISSED`
row (interrogate rounds its percentage to one decimal, so the threshold alone
passes a lone gap in a large repository), and CI must have uv before it runs
`make lint`. These tests read the recipe and the workflow, and also run the
target against a stub tool, so a deleted flag, a dropped prerequisite or a
guard that stops failing breaks a test rather than silently restoring the gap.

The overloaded module `__getattr__` is excluded by name in `pyproject.toml`,
because interrogate does not recognize this repository's `typ.overload` alias.
The exclusion also hides each implementation and everything nested inside it,
so the exclusion is held to that name, and every definition of the name that is
not an overload stub, with its nested definitions, is held to a docstring here.

Run via ``make test``.
"""

from __future__ import annotations

import ast
import operator
import re
import shutil
import subprocess
import tempfile
import textwrap
import tomllib
from pathlib import Path

import hypothesis as hyp
import hypothesis.strategies as st
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
MAKEFILE = ROOT / "Makefile"
CI = ROOT / ".github" / "workflows" / "ci.yml"
OVERLOADED = "__getattr__"
_DEFINITIONS = (ast.AsyncFunctionDef, ast.FunctionDef, ast.ClassDef)


def _makefile_text() -> str:
    """Return the Makefile source."""
    return MAKEFILE.read_text(encoding="utf-8")


def _variable(name: str) -> str:
    """Return the value a `NAME ?=` or `NAME =` assignment gives, joined."""
    pattern = re.compile(rf"^{name}\s*\??=\s*((?:.*\\\n)*.*)$", re.MULTILINE)
    match = pattern.search(_makefile_text())
    assert match, f"the Makefile must assign {name}"
    return re.sub(r"\\\n\s*", " ", match.group(1)).strip()


def _prerequisites(target: str) -> list[str]:
    """Return the prerequisites declared for *target*."""
    pattern = re.compile(rf"^{re.escape(target)}:([^=\n]*)$", re.MULTILINE)
    match = pattern.search(_makefile_text())
    assert match, f"the Makefile must declare a {target} target"
    return match.group(1).split("##")[0].split()


_GENERATED_DIRECTORIES = frozenset({
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "mutants",
    "node_modules",
    "venv",
})


_NOT_A_WORK_TREE = "not a git repository"


def _git_toplevel(git: str, root: Path) -> Path | None:
    """Return the repository root git resolves for *root*, if any.

    A directory outside a work tree is reported as ``None`` so the caller can
    fall back to a walk. Any other failure, including one from the initial
    work-tree query, is unexpected and propagates instead of being mistaken
    for a copied tree.

    Returns
    -------
    Path | None
        The repository root when *root* is inside a work tree, otherwise
        ``None``.

    Raises
    ------
    subprocess.CalledProcessError
        If git fails for a reason other than the directory not being inside a
        work tree.
    """
    inside = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [git, "-C", str(root), "rev-parse", "--is-inside-work-tree"],
        capture_output=True,
        text=True,
        check=False,
    )
    if inside.returncode != 0:
        if _NOT_A_WORK_TREE in inside.stderr:
            return None
        raise subprocess.CalledProcessError(
            inside.returncode, inside.args, inside.stdout, inside.stderr
        )
    if inside.stdout.strip() != "true":
        return None
    toplevel = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [git, "-C", str(root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(toplevel.stdout.strip()).resolve()


def _git_tracked(git: str, root: Path, patterns: tuple[str, ...]) -> list[str]:
    """Return the tracked files matching *patterns*, relative to *root*."""
    output = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [git, "-C", str(root), "ls-files", "-z", *patterns],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [name for name in output.split("\0") if name]


def _copied_sources(root: Path, patterns: tuple[str, ...]) -> list[str]:
    """Return the source files in a copied tree, skipping generated output."""
    names: list[str] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if not path.is_file() or path.is_symlink():
            continue
        if _GENERATED_DIRECTORIES.intersection(relative.parts):
            continue
        if any(path.match(pattern) for pattern in patterns):
            names.append(relative.as_posix())
    return sorted(names)


def _tracked(*patterns: str, root: Path = ROOT) -> list[str]:
    """Return the source files the repository owns, matching *patterns*.

    Git is the authority in a checkout: a walk would also see the virtual
    environments CI creates beside the sources. Names are read from
    NUL-separated output, so a path containing whitespace stays whole.

    Mutmut's sandbox is a copy inside the checkout, where `git ls-files`
    resolves the outer repository and matches nothing. A copied tree therefore
    falls back to a walk that skips generated directories, so the contract
    still inspects the sources instead of passing vacuously.

    Returns
    -------
    list[str]
        The matching paths, relative to *root*.
    """
    git = shutil.which("git")
    assert git, "git must be on the path"
    if _git_toplevel(git, root) == root.resolve():
        return _git_tracked(git, root, patterns)
    return _copied_sources(root, patterns)


def test_lint_depends_on_interrogate() -> None:
    """Dropping the prerequisite would remove the gate from `make lint`."""
    assert "interrogate" in _prerequisites("lint"), "lint must depend on interrogate"


def test_interrogate_is_declared_phony() -> None:
    """A file named `interrogate` must not make the target look up to date."""
    declared = re.findall(r"^\.PHONY:((?:.*\\\n)*.*)$", _makefile_text(), re.MULTILINE)
    names = " ".join(declared).replace("\\\n", " ").split()
    assert "interrogate" in names, "interrogate must be a .PHONY target"


def test_the_tool_is_pinned_and_run_through_the_vendored_helper() -> None:
    """An unpinned tool could change the verdict with no repository change."""
    assert _variable("INTERROGATE_VERSION") == "1.7.0", "the pin must be 1.7.0"
    command = _variable("INTERROGATE")
    assert "$(UV_GATE) tool" in command, "reach uv through the vendored helper"
    assert "--from interrogate@$(INTERROGATE_VERSION)" in command, (
        "the tool must be installed at the pinned version"
    )


def test_the_flags_demand_a_full_exact_report() -> None:
    """Exactly: every non-excluded definition printed, and a threshold of 100.

    Equality, not a deny-list: any other flag (an ignore or exclude option, or
    `-q` and `-o`, which suppress or redirect the report the guard reads) could
    relax the gate or blind the MISSED guard.
    """
    flags = _variable("INTERROGATE_FLAGS").split()
    expected = ["-vv", "--fail-under", "100"]
    assert flags == expected, f"INTERROGATE_FLAGS must be {expected}, not {flags}"


def test_every_python_file_is_under_a_target() -> None:
    """A tracked Python file outside `INTERROGATE_TARGETS` is not checked."""
    targets = [Path(t) for t in _variable("INTERROGATE_TARGETS").split()]
    uncovered = [
        name
        for name in sorted(_tracked("*.py", "*.pyi"))
        if not any(Path(name).is_relative_to(target) for target in targets)
    ]
    assert not uncovered, f"add these to INTERROGATE_TARGETS: {uncovered}"


def test_tracked_files_fall_back_to_a_copied_tree(tmp_path: Path) -> None:
    """A mutation sandbox copy is untracked, so git reports no owned files.

    Mutmut copies the tree into `mutants/` inside the checkout, where
    `git ls-files` resolves the outer repository and matches nothing. The
    contract must still see the copied sources, and must not see caches or
    build outputs.
    """
    sandbox = tmp_path / "mutants"
    (sandbox / "pkg").mkdir(parents=True)
    (sandbox / "pkg" / "mod.py").write_text("", encoding="utf-8")
    (sandbox / "pkg" / "__pycache__").mkdir()
    (sandbox / "pkg" / "__pycache__" / "cached.py").write_text("", encoding="utf-8")
    (sandbox / ".venv" / "lib").mkdir(parents=True)
    (sandbox / ".venv" / "lib" / "site.py").write_text("", encoding="utf-8")
    (sandbox / "linked.py").symlink_to(sandbox / "pkg" / "mod.py")
    (sandbox / "notes.txt").write_text("", encoding="utf-8")

    assert _tracked("*.py", root=sandbox) == ["pkg/mod.py"], (
        "the copied tree must supply the sources without generated output"
    )


def _git(root: Path, *arguments: str) -> None:
    """Run git in *root*, failing the test on error."""
    git = shutil.which("git")
    assert git, "git must be on the path"
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [git, "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        check=True,
    )


def test_tracked_files_come_only_from_the_git_index(tmp_path: Path) -> None:
    """A checkout reports tracked sources, never untracked build output."""
    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "mod.py").write_text("", encoding="utf-8")
    (repo / "untracked.py").write_text("", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", "pkg/mod.py")

    assert _tracked("*.py", root=repo) == ["pkg/mod.py"], (
        "an untracked source must not be reported as owned"
    )


def test_tracked_files_ignore_the_outer_repository_for_a_copied_tree(
    tmp_path: Path,
) -> None:
    """Mutmut's sandbox sits inside the checkout but is not the checkout.

    `git ls-files` resolves the outer repository and reports nothing for the
    copy, so the fallback must supply the copied sources instead.
    """
    repo = tmp_path / "repo"
    (repo / "cmd_mox").mkdir(parents=True)
    (repo / "cmd_mox" / "outer.py").write_text("", encoding="utf-8")
    _git(repo, "init")
    _git(repo, "add", "cmd_mox/outer.py")
    sandbox = repo / "mutants"
    (sandbox / "cmd_mox").mkdir(parents=True)
    (sandbox / "cmd_mox" / "copy.py").write_text("", encoding="utf-8")

    assert _tracked("*.py", root=sandbox) == ["cmd_mox/copy.py"], (
        "the sandbox must read its own copy, not the outer repository"
    )


def _stub_git(tmp_path: Path, script: str) -> str:
    """Return a fake ``git`` executable that runs *script*."""
    stub = tmp_path / "git"
    stub.write_text(f"#!/bin/sh\n{script}", encoding="utf-8")
    stub.chmod(0o755)
    return str(stub)


def test_a_directory_outside_a_work_tree_reports_no_root(tmp_path: Path) -> None:
    """Git's own "not a git repository" message means a copied tree."""
    git = _stub_git(
        tmp_path,
        "echo 'fatal: not a git repository (or any of the parent "
        "directories): .git' >&2\nexit 128\n",
    )

    assert _git_toplevel(git, tmp_path) is None, (
        "a directory outside a work tree must fall back to the walk"
    )


def test_an_unexpected_work_tree_query_failure_propagates(tmp_path: Path) -> None:
    """A permission or configuration error must not look like a copied tree."""
    git = _stub_git(tmp_path, "echo 'fatal: unable to read config' >&2\nexit 128\n")

    with pytest.raises(subprocess.CalledProcessError):
        _git_toplevel(git, tmp_path)


def test_a_failing_root_lookup_propagates(tmp_path: Path) -> None:
    """Once git confirms a work tree, a failing root lookup is an error."""
    git = _stub_git(
        tmp_path,
        'case "$*" in\n'
        "  *--is-inside-work-tree*) echo true ;;\n"
        "  *) echo 'fatal: bad revision' >&2; exit 128 ;;\n"
        "esac\n",
    )

    with pytest.raises(subprocess.CalledProcessError):
        _git_toplevel(git, tmp_path)


_GENERATED_PARTS = frozenset({"__pycache__", ".venv", "mutants"})
_COPIED_DIRECTORIES = st.sampled_from([
    "pkg",
    "pkg/inner",
    "docs",
    "__pycache__",
    ".venv/lib",
    "mutants",
])
_COPIED_FILENAMES = st.sampled_from(["mod.py", "stub.pyi", "notes.txt"])


def _materialise_entry(
    root: Path, target: Path, entry: tuple[str, str, bool]
) -> str | None:
    """Create one generated entry and return its relative name, if visible."""
    parent, name, is_symlink = entry
    path = root / parent / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    if is_symlink:
        path.unlink()
        path.symlink_to(target)
        return None
    if _GENERATED_PARTS.intersection(Path(parent).parts):
        return None
    return path.relative_to(root).as_posix()


_PATTERN_SETS = (("*.py",), ("*.py", "*.pyi"))


@hyp.given(
    entries=st.lists(
        st.tuples(_COPIED_DIRECTORIES, _COPIED_FILENAMES, st.booleans()),
        max_size=10,
        unique_by=operator.itemgetter(0, 1),
    )
)
def test_copied_sources_match_an_independent_filter(
    entries: list[tuple[str, str, bool]],
) -> None:
    """The walk keeps sources outside generated dirs and skips symlinks.

    Hypothesis covers the combinations hand-picked cases miss: nested
    generated directories, non-source files, and links to real sources. Both
    caller pattern sets are checked, so a dropped ``*.pyi`` branch fails.
    """
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        target = root / "target.txt"
        target.write_text("", encoding="utf-8")
        names = {
            name
            for entry in entries
            if (name := _materialise_entry(root, target, entry)) is not None
        }

        for patterns in _PATTERN_SETS:
            expected = sorted(
                name
                for name in names
                if any(Path(name).match(pattern) for pattern in patterns)
            )
            assert _copied_sources(root, patterns) == expected, (
                f"the walk must return {expected} for {patterns} over {entries}"
            )


def test_the_configuration_excludes_only_the_overloaded_name() -> None:
    """The pyproject exclusion is one anchored name, matched wherever it occurs."""
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    settings = config["tool"]["interrogate"]
    assert settings.get("ignore-regex") == [f"^{OVERLOADED}$"], (
        "ignore-regex must name only the overloaded function, anchored"
    )
    forbidden = {"exclude", "ignore-module", "ignore-init-method", "ignore-private"}
    assert not forbidden & set(settings), "no other exclusion may relax the gate"


def test_every_non_overload_definition_of_the_excluded_name_is_documented() -> None:
    """The exclusion hides the overload stubs, so it must not hide a body.

    interrogate skips every class or function of the excluded name in every
    file, each implementation and anything nested in it included, so those
    definitions that are not overload stubs are checked for docstrings here.
    """
    stubs: list[str] = []
    undocumented: list[str] = []
    for name in _tracked("*.py"):
        tree = ast.parse((ROOT / name).read_text(encoding="utf-8"))
        for found in ast.walk(tree):
            if not (isinstance(found, _DEFINITIONS) and found.name == OVERLOADED):
                continue
            if any("overload" in ast.unparse(d) for d in found.decorator_list):
                stubs.append(f"{name}:{found.lineno}")
                continue
            undocumented.extend(
                f"{name}:{node.lineno}"
                for node in ast.walk(found)
                if isinstance(node, _DEFINITIONS) and not ast.get_docstring(node)
            )
    assert stubs, f"expected overload stubs of {OVERLOADED}; is the exclusion stale?"
    assert not undocumented, f"{OVERLOADED} needs docstrings at {undocumented}"


def test_ci_installs_uv_before_it_runs_make_lint() -> None:
    """Each ci.yml job with an exact `make lint` step has uv first; one exists."""
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    checked = 0
    for name, job in workflow["jobs"].items():
        steps = job.get("steps", [])
        lint = [
            i
            for i, s in enumerate(steps)
            if str(s.get("run", "")).strip() == "make lint"
        ]
        if not lint:
            continue
        uv = [i for i, s in enumerate(steps) if "setup-uv" in str(s.get("uses", ""))]
        assert uv, f"job {name} runs make lint without setting up uv"
        assert min(uv) < lint[0], f"job {name} sets up uv after make lint"
        checked += 1
    assert checked, "no CI job runs `make lint`"


def _run_target(tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
    """Run `make interrogate` with the tool replaced by a stub script."""
    stub = tmp_path / "stub.sh"
    stub.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$(dirname "$0")/args.txt"\n'
        + textwrap.dedent(script),
        encoding="utf-8",
    )
    stub.chmod(0o755)
    make = shutil.which("make")
    assert make, "make must be on the path"
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [make, "-C", str(ROOT), "interrogate", f"INTERROGATE={stub}"],
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_target_passes_when_the_tool_reports_no_gap(tmp_path: Path) -> None:
    """A clean report and a zero status pass."""
    result = _run_target(tmp_path, "echo '| f.py | COVERED |'\nexit 0\n")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "| f.py | COVERED |" in result.stdout, "the report must be printed"


def test_the_target_passes_the_configured_flags_and_targets(tmp_path: Path) -> None:
    """The tool receives exactly the configured flags, then the targets."""
    _run_target(tmp_path, "exit 0\n")
    received = (tmp_path / "args.txt").read_text(encoding="utf-8").split()
    expected = (
        _variable("INTERROGATE_FLAGS").split()
        + _variable("INTERROGATE_TARGETS").split()
    )
    assert received == expected, f"the tool received {received}, not {expected}"


def test_the_target_fails_when_the_tool_fails(tmp_path: Path) -> None:
    """A failing tool fails the target."""
    result = _run_target(tmp_path, "echo 'FAILED'\nexit 1\n")
    assert result.returncode != 0, "a failing tool must fail the target"


@pytest.mark.parametrize(
    "cell",
    ["|  MISSED |", "|   MISSED    |"],
    ids=["interrogate-default-padding", "wider-padding"],
)
def test_the_target_fails_on_a_missed_row_the_percentage_hid(
    tmp_path: Path, cell: str
) -> None:
    """A MISSED row in a report from a successful run still fails the target.

    The guard must not depend on how many spaces pad the status cell.
    """
    result = _run_target(
        tmp_path,
        f"echo '| f.main (L3) {cell}'\necho 'RESULT: PASSED'\nexit 0\n",
    )
    assert result.returncode != 0, "a MISSED row must fail the target"
    assert "undocumented definitions remain" in result.stderr, result.stderr
