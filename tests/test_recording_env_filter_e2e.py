"""End-to-end recording tests through the generated shim and IPC server.

These tests deliberately avoid the controller's private passthrough helpers:
the environment a recording filters is the one the shim observes in its own
process, so only a real shim invocation proves the wiring between the shim,
the IPC server, and the persisted fixture.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.record.fixture import FixtureFile

pytestmark = [pytest.mark.requires_unix_sockets]

#: Go variables recorded by default; ``GOOGLE_CLOUD_PROJECT`` is a lookalike
#: that the old ``GO`` prefix recorded but the exact-name rule now drops.
_DEFAULT_SUBSET = {"GOPATH": "/work", "GORACE": "halt_on_error=1"}
_ALLOWLISTED_SUBSET = {**_DEFAULT_SUBSET, "GOOGLE_CLOUD_PROJECT": "proj"}


def _shim_environment() -> dict[str, str]:
    """Return a child environment carrying only cmd-mox plumbing and Go keys.

    The baseline process environment is intentionally not inherited, so the
    recorded subset is determined by the filter rather than by whatever Go
    variables the machine running the tests happens to export.

    Returns
    -------
    dict[str, str]
        Environment for the shim subprocess, including the real-command
        override that points at the current interpreter.
    """
    plumbing = {
        name: value for name, value in os.environ.items() if name.startswith("CMOX_")
    }
    return {
        **plumbing,
        **_ALLOWLISTED_SUBSET,
        "CMOX_REAL_COMMAND_go": sys.executable,
        "PATH": os.environ["PATH"],
    }


@pytest.mark.parametrize(
    ("env_allowlist", "expected"),
    [
        pytest.param(None, _DEFAULT_SUBSET, id="default"),
        pytest.param(["GOOGLE_CLOUD_PROJECT"], _ALLOWLISTED_SUBSET, id="allowlisted"),
    ],
)
def test_go_environment_is_filtered_through_the_shim(
    tmp_path: Path,
    env_allowlist: list[str] | None,
    expected: dict[str, str],
) -> None:
    """A shim invocation records the filtered Go environment to its fixture."""
    fixture_path = tmp_path / "go.json"
    with CmdMox() as mox:
        mox.spy("go").passthrough().record(fixture_path, env_allowlist=env_allowlist)
        mox.replay()

        shim_path = shutil.which("go")
        assert shim_path is not None, "the go shim must be on PATH"
        assert Path(shim_path).parent == mox.environment.shim_dir, (
            "go must resolve to the generated shim, not a system binary"
        )

        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - the test executes a command path prepared by the test harness
            [shim_path, "-c", "print('ok')"],
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            env=_shim_environment(),
        )

        assert result.returncode == 0, result.stderr
        mox.verify()

    fixture = FixtureFile.load(fixture_path)
    assert fixture.recordings[0].command == "go", "the shim recorded another command"
    assert fixture.recordings[0].env_subset == expected, (
        f"unexpected recorded environment: {fixture.recordings[0].env_subset}"
    )
