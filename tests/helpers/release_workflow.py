"""Shared helpers for checked-in release workflow tests.

Call ``load_action_build_step`` to read the script and environment from the
composite action, then pass them to ``run_bash_step`` with a scratch directory
and controlled environment. Use ``write_executable`` to install fake tools on
that environment's ``PATH``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import yaml


def load_action_build_step() -> tuple[str, dict[str, str]]:
    """Return the build command and environment from the pure-Python action.

    Returns
    -------
    tuple[str, dict[str, str]]
        The build step's shell script and environment mapping.
    """
    action_path = (
        Path(__file__).resolve().parents[2]
        / ".github"
        / "actions"
        / "pure-python-wheel"
        / "action.yml"
    )
    action = yaml.safe_load(action_path.read_text(encoding="utf-8"))
    steps = action["runs"]["steps"]
    step = next(item for item in steps if item.get("id") == "build")
    return step["run"], step["env"]


def run_bash_step(
    script: str,
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run a shell step in a scratch directory with a controlled environment.

    Parameters
    ----------
    script : str
        Bash script extracted from a checked-in workflow or action.
    cwd : pathlib.Path
        Scratch working directory for the command.
    env : dict[str, str]
        The complete environment passed to Bash and its child processes.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process, including captured output and status.
    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed Bash argv runs the checked-in action script.
        [shutil.which("bash") or "bash", "-c", script],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        shell=False,
        text=True,
        timeout=30,
    )


def write_executable(path: Path, contents: str) -> None:
    """Write a shell stub and mark it executable.

    Parameters
    ----------
    path : pathlib.Path
        Location of the executable stub.
    contents : str
        Script text, including its interpreter shebang.
    """
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)
