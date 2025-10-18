"""Pytest suite used to validate cmd_mox isolation under pytest-xdist."""

from __future__ import annotations

import json
import os
import subprocess
import typing as t
from pathlib import Path

from cmd_mox.unittests.test_invocation_journal import _shim_cmd_path

pytest_plugins = ("cmd_mox.pytest_plugin",)

if t.TYPE_CHECKING:  # pragma: no cover - imported for type checking only
    from cmd_mox.controller import CmdMox


class MissingParallelArtifactDirError(RuntimeError):
    """Raised when the parallel suite runs without its artifact directory."""


def _artifact_dir() -> Path:
    """Return the directory for recording worker metadata."""
    try:
        root = Path(os.environ["CMD_MOX_PARALLEL_ARTIFACT_DIR"])
    except KeyError as exc:  # pragma: no cover - defensive guard for template misuse
        raise MissingParallelArtifactDirError from exc
    root.mkdir(parents=True, exist_ok=True)
    return root


def _record(cmd_mox: CmdMox, label: str) -> None:
    """Write shim/socket metadata for the given expectation and invoke it."""
    artifact_dir = _artifact_dir()
    shim_dir = Path(cmd_mox.environment.shim_dir)
    socket_path = Path(cmd_mox.environment.socket_path)
    payload = {
        "label": label,
        "shim_dir": str(shim_dir),
        "socket": str(socket_path),
        "worker": os.getenv("PYTEST_XDIST_WORKER", "main"),
    }
    artifact = artifact_dir / f"{label}-{os.getpid()}-{payload['worker']}.json"
    artifact.write_text(json.dumps(payload))
    cmd_path = _shim_cmd_path(cmd_mox, label)
    result = subprocess.run(  # noqa: S603 - command path derives from the shim setup
        [str(cmd_path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == label  # noqa: S101 - executed as a pytest test


def test_alpha(cmd_mox: CmdMox) -> None:
    """Record shim/socket metadata for the alpha expectation."""
    cmd_mox.stub("alpha").returns(stdout="alpha")
    _record(cmd_mox, "alpha")


def test_beta(cmd_mox: CmdMox) -> None:
    """Record shim/socket metadata for the beta expectation."""
    cmd_mox.stub("beta").returns(stdout="beta")
    _record(cmd_mox, "beta")
