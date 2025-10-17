"""Helpers for pytest plugin tests."""

from __future__ import annotations

import dataclasses as dc
import json
import textwrap
from pathlib import Path

PARALLEL_SUITE = textwrap.dedent(
    """
    import json
    import os
    import subprocess
    from pathlib import Path

    from cmd_mox.unittests.test_invocation_journal import _shim_cmd_path

    pytest_plugins = ("cmd_mox.pytest_plugin",)

    ARTIFACT_DIR = Path(__file__).with_name("artifacts")

    def _record(cmd_mox, label: str) -> None:
        ARTIFACT_DIR.mkdir(exist_ok=True)
        shim_dir = Path(cmd_mox.environment.shim_dir)
        socket_path = Path(cmd_mox.environment.socket_path)
        payload = {
            "label": label,
            "shim_dir": str(shim_dir),
            "socket": str(socket_path),
            "worker": os.getenv("PYTEST_XDIST_WORKER", "main"),
        }
        artifact = ARTIFACT_DIR / f"{label}-{os.getpid()}-{payload['worker']}.json"
        artifact.write_text(json.dumps(payload))
        cmd_path = _shim_cmd_path(cmd_mox, label)
        result = subprocess.run(
            [str(cmd_path)],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == label

    def test_alpha(cmd_mox):
        cmd_mox.stub("alpha").returns(stdout="alpha")
        _record(cmd_mox, "alpha")

    def test_beta(cmd_mox):
        cmd_mox.stub("beta").returns(stdout="beta")
        _record(cmd_mox, "beta")
    """
)


@dc.dataclass(slots=True)
class ParallelRecord:
    """Representation of a recorded parallel test run."""

    label: str
    shim_dir: Path
    socket: Path
    worker: str


def read_parallel_records(artifact_dir: Path) -> list[ParallelRecord]:
    """Return parsed records written by the parallel isolation suite."""
    records: list[ParallelRecord] = []
    for path in sorted(artifact_dir.glob("*.json")):
        payload = json.loads(path.read_text())
        records.append(
            ParallelRecord(
                label=payload["label"],
                shim_dir=Path(payload["shim_dir"]),
                socket=Path(payload["socket"]),
                worker=payload["worker"],
            )
        )
    return records


__all__ = ["PARALLEL_SUITE", "ParallelRecord", "read_parallel_records"]
