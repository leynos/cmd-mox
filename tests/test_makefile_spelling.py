"""Tests for the repository spelling target."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_GIT_REPOSITORY_LOCATION_VARIABLES = frozenset({
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
})


def _run_command(
    arguments: list[str], *, cwd: Path, environment: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    """Run a fixed local command without invoking a shell.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process.
    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed local tools and argv, no shell.
        arguments,
        capture_output=True,
        check=False,
        cwd=cwd,
        env=environment,
        shell=False,
        text=True,
    )


def _prepare_project(project: Path) -> tuple[Path, Path, Path, dict[str, str]]:
    """Create test files and stubs for the isolated spelling command.

    Returns
    -------
    tuple[Path, Path, Path, dict[str, str]]
        The argument capture path, uv stub, Typos stub, and their environment.
    """
    project.mkdir()
    (project / ".gitignore").write_text("ignored.md\n", encoding="utf-8")
    (project / "tracked.md").write_text("tracked\n", encoding="utf-8")
    (project / "deleted.md").write_text("deleted\n", encoding="utf-8")
    (project / "untracked.md").write_text("untracked\n", encoding="utf-8")
    (project / "ignored.md").write_text("ignored\n", encoding="utf-8")

    captured_arguments = project / "typos-arguments.json"
    typos_stub = project / "typos-stub.py"
    typos_stub.write_text(
        "import json\n"
        "import os\n"
        "import sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['SPELLING_CAPTURE']).write_text(\n"
        "    json.dumps(sys.argv[1:]), encoding='utf-8'\n"
        ")\n",
        encoding="utf-8",
    )

    uv_gate_stub = project / "uv-gate-stub.py"
    uv_gate_stub.write_text(
        "import os\n"
        "import sys\n"
        "arguments = sys.argv[1:]\n"
        "command = arguments[arguments.index('--') + 1:]\n"
        "if command[-1].endswith('generate_typos_config.py'):\n"
        "    raise SystemExit(0)\n"
        "os.execv(sys.executable, [sys.executable, *command[1:]])\n",
        encoding="utf-8",
    )

    environment = os.environ.copy()
    for name in tuple(environment):
        if name.startswith("GIT_CONFIG_") or (
            name in _GIT_REPOSITORY_LOCATION_VARIABLES
        ):
            environment.pop(name)
    environment.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "SPELLING_CAPTURE": str(captured_arguments),
    })
    return captured_arguments, uv_gate_stub, typos_stub, environment


def _make_spelling_command(uv_gate_stub: Path, typos_stub: Path) -> list[str]:
    """Build the isolated Make invocation for the spelling target.

    Returns
    -------
    list[str]
        The Make command and its arguments.
    """
    make_executable = shutil.which("make")
    assert make_executable is not None, "make must be available for the spelling test"
    makefile = Path(__file__).resolve().parents[1] / "Makefile"
    return [
        make_executable,
        "--no-print-directory",
        "-f",
        str(makefile),
        "spelling",
        f'UV_GATE="{sys.executable}" "{uv_gate_stub}"',
        f'TYPOS="{sys.executable}" "{typos_stub}"',
    ]


def _run_spelling_target(
    project: Path,
    uv_gate_stub: Path,
    typos_stub: Path,
    environment: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run ``make spelling`` in the supplied working directory.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed Make invocation.
    """
    return _run_command(
        _make_spelling_command(uv_gate_stub, typos_stub),
        cwd=project,
        environment=environment,
    )


def _initialize_project(project: Path, environment: dict[str, str]) -> None:
    """Stage tracked Markdown and leave one staged path deleted in the worktree."""
    git_executable = shutil.which("git")
    assert git_executable is not None, "git must be available for the spelling test"
    initialized = _run_command(
        [git_executable, "init", "--quiet"], cwd=project, environment=environment
    )
    assert initialized.returncode == 0, f"git init failed: {initialized.stderr}"
    staged = _run_command(
        [git_executable, "add", ".gitignore", "tracked.md", "deleted.md"],
        cwd=project,
        environment=environment,
    )
    assert staged.returncode == 0, f"git add failed: {staged.stderr}"
    (project / "deleted.md").unlink()


def test_spelling_target_scans_existing_tracked_and_unignored_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass existing tracked and unignored Markdown paths to the spelling tool."""
    external_git_paths = {
        "GIT_COMMON_DIR": tmp_path / "external-common",
        "GIT_DIR": tmp_path / "external-git",
        "GIT_INDEX_FILE": tmp_path / "external-index",
        "GIT_WORK_TREE": tmp_path / "external-worktree",
    }
    for name, path in external_git_paths.items():
        monkeypatch.setenv(name, str(path))

    project = tmp_path / "project"
    captured_arguments, uv_stub, typos_stub, environment = _prepare_project(project)
    _initialize_project(project, environment)

    spelling = _run_spelling_target(project, uv_stub, typos_stub, environment)
    assert spelling.returncode == 0, f"make spelling failed: {spelling.stderr}"
    assert (project / ".git" / "index").is_file(), "Git must use the project index"
    assert not any(path.exists() for path in external_git_paths.values()), (
        "Git must not create repository state outside the isolated project"
    )
    arguments = json.loads(captured_arguments.read_text(encoding="utf-8"))
    scanned_markdown = {argument for argument in arguments if argument.endswith(".md")}

    assert scanned_markdown == {"tracked.md", "untracked.md"}, (
        f"unexpected Markdown paths passed to Typos: {scanned_markdown!r}"
    )


def test_spelling_target_propagates_git_listing_failure(tmp_path: Path) -> None:
    """Fail when Git cannot list repository Markdown files."""
    project = tmp_path / "outside-git"
    captured_arguments, uv_stub, typos_stub, environment = _prepare_project(project)

    spelling = _run_spelling_target(project, uv_stub, typos_stub, environment)

    assert spelling.returncode != 0, "make spelling must fail outside a Git repository"
    assert not captured_arguments.exists(), "Typos must not run after Git listing fails"
