"""Shared helpers for checked-in release workflow tests.

Call ``load_workflow_step`` or ``load_action_build_step`` to read a checked-in
script and its environment, then pass the script to ``run_bash_step`` with a
scratch directory and controlled environment. Use ``write_executable`` and
``install_fake_tools`` to put recording ``gh``, ``curl`` and ``sleep`` stubs
first on ``PATH``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import typing as typ
from pathlib import Path

import yaml

ActionInputs = typ.TypedDict(
    "ActionInputs",
    {
        "artifact-name": typ.NotRequired[str],
        "if-no-files-found": typ.NotRequired[str],
        "name": typ.NotRequired[str],
        "packages-dir": typ.NotRequired[str],
        "persist-credentials": typ.NotRequired[bool],
        "python-version": typ.NotRequired[str],
        "skip-existing": typ.NotRequired[bool],
    },
    total=False,
)
WorkflowStep = typ.TypedDict(
    "WorkflowStep",
    {
        "env": typ.NotRequired[dict[str, str]],
        "id": typ.NotRequired[str],
        "if": typ.NotRequired[str],
        "name": typ.NotRequired[str],
        "run": typ.NotRequired[str],
        "uses": typ.NotRequired[str],
        "with": typ.NotRequired[ActionInputs],
    },
    total=False,
)


class PushTrigger(typ.TypedDict, total=False):
    """Optional tag filter used by a push workflow trigger."""

    tags: list[str]


class WorkflowTriggers(typ.TypedDict, total=False):
    """Workflow trigger mappings used by the release and CI contracts."""

    push: PushTrigger
    pull_request: object


class WorkflowJob(typ.TypedDict):
    """Workflow job fields used by release contract tests."""

    steps: list[WorkflowStep]
    needs: typ.NotRequired[str | list[str]]
    permissions: typ.NotRequired[dict[str, str]]
    environment: typ.NotRequired[dict[str, str]]


class Workflow(typ.TypedDict):
    """Workflow document fields used by the checked-in contract tests."""

    jobs: dict[str, WorkflowJob]
    permissions: typ.NotRequired[dict[str, object]]
    concurrency: typ.NotRequired[dict[str, object]]


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RELEASE_WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"


def should_skip_release_tests_for_mutation_sandbox(
    working_root: Path, workflow_path: Path, cli_path: Path
) -> bool:
    """Skip release-step tests only in mutmut's sparse working copy.

    Parameters
    ----------
    working_root : pathlib.Path
        The current pytest working root.
    workflow_path : pathlib.Path
        The checked-in release workflow path for this copy.
    cli_path : pathlib.Path
        The release-assets CLI path for this copy.

    Returns
    -------
    bool
        Whether the mutmut sandbox omits either required release file.
    """
    return working_root.name == "mutants" and not (
        workflow_path.is_file() and cli_path.is_file()
    )


def load_workflow() -> Workflow:
    """Parse the release workflow with PyYAML's YAML 1.1 key compatibility.

    Returns
    -------
    Workflow
        Workflow mapping as returned by ``yaml.safe_load``.
    """
    return typ.cast(
        "Workflow", yaml.safe_load(RELEASE_WORKFLOW_PATH.read_text(encoding="utf-8"))
    )


def workflow_triggers(workflow: Workflow) -> WorkflowTriggers:
    """Return the workflow's ``on:`` mapping for YAML 1.1 or 1.2 parsers.

    Parameters
    ----------
    workflow : Workflow
        Parsed workflow document.

    Returns
    -------
    WorkflowTriggers
        Trigger mapping under either the string or boolean YAML key.

    Raises
    ------
    TypeError
        If the parsed workflow does not contain a mapping of triggers.
    """
    workflow_map = typ.cast("dict[object, object]", workflow)
    triggers = workflow_map.get("on", workflow_map.get(True))
    match triggers:
        case dict():
            return typ.cast("WorkflowTriggers", triggers)
        case _:
            raise TypeError


def load_workflow_step(job_id: str, step_id_or_name: str) -> tuple[str, dict[str, str]]:
    """Return a workflow step's shell script and declared environment.

    Parameters
    ----------
    job_id : str
        Job key in ``jobs``.
    step_id_or_name : str
        Step's ``id`` or exact ``name`` value.

    Returns
    -------
    tuple[str, dict[str, str]]
        The step's ``run`` script and environment mapping.
    """
    workflow = load_workflow()
    job = workflow["jobs"][job_id]
    step = next(
        item
        for item in job["steps"]
        if item.get("id") == step_id_or_name or item.get("name") == step_id_or_name
    )
    return step["run"], step.get("env", {})


def load_action_build_step() -> tuple[str, dict[str, str]]:
    """Return the build command and environment from the pure-Python action.

    Returns
    -------
    tuple[str, dict[str, str]]
        The build step's shell script and environment mapping.
    """
    action_path = (
        REPOSITORY_ROOT / ".github" / "actions" / "pure-python-wheel" / "action.yml"
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
    output_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a shell step with controlled tools and the test Python interpreter.

    Parameters
    ----------
    script : str
        Bash script extracted from a checked-in workflow or action.
    cwd : pathlib.Path
        Scratch working directory for the command.
    env : dict[str, str]
        The complete environment passed to Bash and its child processes.
    output_path : pathlib.Path or None, optional
        File used for ``GITHUB_OUTPUT``; defaults to ``cwd/github-output``.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process, including captured output and status.
    """
    python_shim = cwd / ".python-shim"
    python_shim.mkdir(exist_ok=True)
    python3 = python_shim / "python3"
    if not python3.exists():
        python3.symlink_to(sys.executable)
    process_env = {
        **env,
        "PATH": os.pathsep.join((str(python_shim), env.get("PATH", ""))),
        "GITHUB_OUTPUT": str(
            output_path or env.get("GITHUB_OUTPUT") or cwd / "github-output"
        ),
        "PYTHONPATH": str(REPOSITORY_ROOT),
    }
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed Bash argv runs the checked-in workflow script.
        [shutil.which("bash") or "bash", "-c", script],
        cwd=cwd,
        env=process_env,
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


_FAKE_GH = r"""#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path

argv = sys.argv[1:]
log_path = Path(os.environ["FAKE_TOOL_LOG"])
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps({"tool": "gh", "argv": argv}) + "\n")

if argv[:2] != ["release", "view"]:
    if argv[:2] == ["release", "create"] or argv[:2] == ["release", "edit"]:
        raise SystemExit(0)
    if argv[:2] == ["release", "download"]:
        pattern = argv[argv.index("--pattern") + 1]
        directory = Path(argv[argv.index("--dir") + 1])
        source = Path(os.environ["GH_DOWNLOADS"]) / pattern
        directory.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, directory / pattern)
        raise SystemExit(0)
    if argv[:2] == ["release", "upload"]:
        raise SystemExit(0)
    raise SystemExit(2)

if os.environ.get("GH_RELEASE_EXISTS", "1") != "1":
    sys.stderr.write("release not found\n")
    raise SystemExit(1)
release_state = os.environ.get("GH_RELEASE_STATE")
if release_state is not None:
    if release_state not in {"draft", "published"}:
        raise SystemExit(2)
    state_log = os.environ.get("GH_RELEASE_STATE_LOG")
    if state_log is not None:
        Path(state_log).write_text(release_state, encoding="utf-8")
if "--json" in argv:
    sys.stdout.write(os.environ.get("GH_ASSETS_JSON", '{"assets": []}'))
    sys.stdout.write("\n")
"""

_FAKE_CURL = r"""#!/usr/bin/env python3
import json
import os
import sys
import urllib.parse
from pathlib import Path

argv = sys.argv[1:]
log_path = Path(os.environ["FAKE_TOOL_LOG"])
calls = []
if log_path.exists():
    calls = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
curl_count = sum(call["tool"] == "curl" for call in calls)
with log_path.open("a", encoding="utf-8") as log:
    log.write(json.dumps({"tool": "curl", "argv": argv}) + "\n")

statuses = os.environ.get("CURL_STATUSES", "200").split(",")
status = statuses[min(curl_count, len(statuses) - 1)]
bodies = json.loads(os.environ["CURL_BODIES"]) if "CURL_BODIES" in os.environ else None
if "--output" in argv:
    output = Path(argv[argv.index("--output") + 1])
    output.parent.mkdir(parents=True, exist_ok=True)
    body_text = (
        bodies[min(curl_count, len(bodies) - 1)]
        if bodies is not None
        else os.environ.get("CURL_BODY", '{"files": []}')
    )
    body = body_text.encode()
    if "--write-out" not in argv and "CURL_DOWNLOADS" in os.environ:
        name = Path(urllib.parse.urlparse(argv[-1]).path).name
        download = Path(os.environ["CURL_DOWNLOADS"]) / name
        if download.is_file():
            body = download.read_bytes()
    output.write_bytes(body if status == "200" else b"")
if "--write-out" in argv:
    sys.stdout.write(status)
if status == "000":
    raise SystemExit(7)
if "--fail" in argv and status.startswith(("4", "5")):
    raise SystemExit(22)
"""

_FAKE_SLEEP = r"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

with Path(os.environ["FAKE_TOOL_LOG"]).open("a", encoding="utf-8") as log:
    log.write(json.dumps({"tool": "sleep", "argv": sys.argv[1:]}) + "\n")
"""


def install_fake_tools(directory: Path) -> Path:
    """Install recording ``gh``, ``curl`` and immediate ``sleep`` commands.

    Parameters
    ----------
    directory : pathlib.Path
        Directory where the executable stubs are written.

    Returns
    -------
    pathlib.Path
        The directory to prepend to a shell step's ``PATH``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    write_executable(directory / "gh", _FAKE_GH)
    write_executable(directory / "curl", _FAKE_CURL)
    write_executable(directory / "sleep", _FAKE_SLEEP)
    return directory


def read_tool_calls(path: Path, *, tool: str | None = None) -> list[dict[str, object]]:
    """Read recorded fake-tool argv, optionally filtering by command name.

    Parameters
    ----------
    path : pathlib.Path
        JSON-lines log produced by ``install_fake_tools``.
    tool : str or None, optional
        Tool name to filter on, such as ``gh`` or ``curl``.

    Returns
    -------
    list[dict[str, object]]
        Recorded tool calls in invocation order.
    """
    if not path.exists():
        return []
    calls = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return [call for call in calls if tool is None or call["tool"] == tool]
