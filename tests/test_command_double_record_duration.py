"""End-to-end test that ``.record()`` measures real command duration.

The duration is measured by the passthrough coordinator, so only a real shim
invocation proves the wiring between the shim, the IPC server, and the
persisted fixture. The test deliberately avoids the controller's private
passthrough helpers.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import typing as typ

from cmd_mox import _path_utils as path_utils
from cmd_mox.controller import CmdMox
from cmd_mox.environment import CMOX_REAL_COMMAND_ENV_PREFIX
from cmd_mox.record.fixture import FixtureFile
from tests.helpers.controller import RUN_TIMEOUT_SECONDS, escape_windows_batch_args

if typ.TYPE_CHECKING:
    from pathlib import Path

    import pytest

#: Sleep long enough that a plausible duration is well above timer noise.
_SLEEP_SECONDS = 0.3
#: Generous lower bound: the measured interval includes IPC overhead.
_MIN_DURATION_MS = 200


def _resolve_shim(name: str) -> str:
    """Return the generated shim path, including the Windows ``.cmd`` variant."""
    resolved = shutil.which(name)
    if resolved is None and path_utils.IS_WINDOWS:
        resolved = shutil.which(f"{name}.cmd")
    assert resolved is not None, f"the {name} shim must be on PATH"
    return resolved


def test_recorded_passthrough_reports_real_duration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A recorded sleep reports a plausible non-zero wall-clock duration."""
    monkeypatch.setenv(f"{CMOX_REAL_COMMAND_ENV_PREFIX}slow", sys.executable)
    fixture_path = tmp_path / "slow.json"
    code = f"import time; time.sleep({_SLEEP_SECONDS}); print('slept')"

    with CmdMox() as mox:
        mox.spy("slow").passthrough().record(fixture_path)
        mox.replay()

        argv = escape_windows_batch_args([_resolve_shim("slow"), "-c", code])
        result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - the test executes a command path prepared by the test harness
            argv,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
            stdin=subprocess.DEVNULL,
            timeout=RUN_TIMEOUT_SECONDS,
        )

        assert result.returncode == 0, result.stderr
        mox.verify()

    fixture = FixtureFile.load(fixture_path)
    recording = fixture.recordings[0]
    assert recording.command == "slow", "the shim recorded another command"
    assert recording.args == ["-c", code], "the shim recorded different arguments"
    assert recording.stdout.strip() == "slept", "the real command's output was lost"
    assert recording.exit_code == 0, "the real command did not exit cleanly"
    assert recording.duration_ms >= _MIN_DURATION_MS, (
        f"recorded duration {recording.duration_ms} ms is implausibly short"
    )
