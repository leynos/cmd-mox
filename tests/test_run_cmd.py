"""Unit tests for the :func:`tests.helpers.run_cmd` helper."""

import os
import subprocess
import sys

import pytest

from tests.helpers import run_cmd


def test_run_cmd_success() -> None:
    """Command runs successfully and returns captured output."""
    result = run_cmd([sys.executable, "-c", "print('ok')"])
    assert result.stdout.strip() == "ok"
    assert result.stderr == ""
    assert result.returncode == 0


def test_run_cmd_kwargs_propagation() -> None:
    """``input``, ``env`` and ``shell`` args are passed through."""
    script = (
        "import os,sys; data=sys.stdin.read(); "
        "sys.stdout.write(data + os.environ.get('X',''))"
    )
    result = run_cmd(
        [sys.executable, "-c", script],
        input="hi",
        env={**os.environ, "X": "there"},
    )
    assert result.stdout == "hithere"

    result2 = run_cmd(["echo shell"], shell=True)  # noqa: S604
    assert result2.stdout.strip() == "shell"


def test_run_cmd_failure_and_override() -> None:
    """Non-zero exit codes raise unless ``check=False`` is set."""
    cmd = [sys.executable, "-c", "import sys; sys.exit(3)"]
    with pytest.raises(subprocess.CalledProcessError):
        run_cmd(cmd)

    result = run_cmd(cmd, check=False)
    assert result.returncode == 3
