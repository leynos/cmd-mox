"""Boundary tests: run the Make targets against a controlled repository.

The recipes run with stand-in tools in throwaway Git repositories to prove
that exactly the tracked YAML reaches ``yamllint --strict`` and that either
tool's failure fails its target. When ``yamllint`` is installed, the real tool
runs with the estate configuration: a tracked finding fails the gate and an
untracked one does not.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from .support import REPO_ROOT


def run(program: str, *arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run an executable found on ``PATH`` with a fixed argument list.

    Parameters
    ----------
    program : str
        The executable's name, resolved to a full path first.
    *arguments : str
        The arguments; no shell is involved.
    cwd : Path
        The directory to run in.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished process, output captured and the status not checked.
    """
    executable = shutil.which(program)
    assert executable is not None, f"{program} is not on PATH"
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv, no shell
        [executable, *arguments], cwd=cwd, capture_output=True, text=True, check=False
    )


def run_make(target: str, cwd: Path, **tools: str) -> subprocess.CompletedProcess[str]:
    """Run one Make target of this repository's Makefile inside ``cwd``.

    Parameters
    ----------
    target : str
        The target to build.
    cwd : Path
        The repository the recipe runs in.
    **tools : str
        Make variable overrides, such as ``YAMLLINT`` and ``ACTIONLINT``.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The finished process.
    """
    overrides = [f"{name}={value}" for name, value in tools.items()]
    return run("make", "-f", str(REPO_ROOT / "Makefile"), target, *overrides, cwd=cwd)


def fake_tool(path: Path, exit_code: int) -> Path:
    """Write an executable stand-in that logs its arguments and exits.

    Parameters
    ----------
    path : Path
        Where to write the tool; its log is ``<path>.log``.
    exit_code : int
        The status the tool exits with.

    Returns
    -------
    Path
        ``path``, made executable.
    """
    path.write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > "{path}.log"\nexit {exit_code}\n',
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def git_repo(root: Path, tracked: dict[str, str], untracked: dict[str, str]) -> Path:
    """Create a Git repository with tracked and untracked files.

    Parameters
    ----------
    root : Path
        The directory to initialise.
    tracked : dict[str, str]
        Files to create and stage, by path.
    untracked : dict[str, str]
        Files to create and leave untracked, by path.

    Returns
    -------
    Path
        ``root``.
    """
    root.mkdir(parents=True, exist_ok=True)
    run("git", "init", "-q", cwd=root)
    for name, text in {**tracked, **untracked}.items():
        (root / name).write_text(text, encoding="utf-8")
    run("git", "add", *tracked, cwd=root)
    return root


def test_make_yamllint_lints_the_tracked_yaml_only_and_strictly(tmp_path: Path) -> None:
    """The recipe hands ``--strict`` and exactly the tracked YAML to the tool."""
    repo = git_repo(
        tmp_path / "repo", {"a.yml": "a: 1\n", "b.yaml": "b: 1\n"}, {"c.yml": "c: 1\n"}
    )
    tool = fake_tool(tmp_path / "yamllint", 0)

    result = run_make("yamllint", repo, YAMLLINT=str(tool))

    assert result.returncode == 0, result.stderr
    logged = Path(f"{tool}.log").read_text(encoding="utf-8").split()
    assert logged[0] == "--strict", logged
    assert sorted(logged[1:]) == ["--", "a.yml", "b.yaml"], logged


@pytest.mark.parametrize("target", ["yamllint", "actionlint"])
def test_a_failing_tool_fails_its_make_target(tmp_path: Path, target: str) -> None:
    """The tool's exit status reaches Make, so a finding stops the gate."""
    repo = git_repo(tmp_path / "repo", {"a.yml": "a: 1\n"}, {})
    tool = fake_tool(tmp_path / target, 1)

    result = run_make(target, repo, **{target.upper(): str(tool)})

    assert result.returncode != 0, "a failing tool did not fail the target"


def test_make_lint_reaches_both_tools(tmp_path: Path) -> None:
    """A dry run of ``lint`` names both tools, so the closure includes them."""
    repo = git_repo(tmp_path / "repo", {"a.yml": "a: 1\n"}, {})

    result = run("make", "-n", "-f", str(REPO_ROOT / "Makefile"), "lint", cwd=repo)

    assert "yamllint --strict" in result.stdout, result.stdout
    assert "\nactionlint\n" in f"\n{result.stdout}", result.stdout


@pytest.mark.skipif(
    shutil.which("yamllint") is None, reason="yamllint is not installed"
)
def test_the_real_yamllint_fails_strictly_on_a_tracked_warning_only(
    tmp_path: Path,
) -> None:
    """A tracked finding fails the gate and an untracked one does not."""
    repo = git_repo(
        tmp_path / "repo",
        {"good.yml": "key: value\n", "bad.yml": "key: value \n"},
        {"untracked-bad.yml": "key: value \n"},
    )
    for name in (".yamllint-estate.yml", ".yamllint.yml"):
        shutil.copy(REPO_ROOT / name, repo / name)
    run("git", "add", ".yamllint-estate.yml", ".yamllint.yml", cwd=repo)

    failing = run_make("yamllint", repo)
    run("git", "rm", "-q", "--cached", "-f", "bad.yml", cwd=repo)
    passing = run_make("yamllint", repo)

    assert failing.returncode != 0, "a tracked finding did not fail the gate"
    assert "bad.yml" in failing.stdout, failing.stdout
    assert "untracked-bad.yml" not in failing.stdout, failing.stdout
    assert passing.returncode == 0, passing.stdout
