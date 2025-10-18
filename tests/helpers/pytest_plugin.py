"""Helpers for pytest plugin tests."""

from __future__ import annotations

import dataclasses as dc
import json
from pathlib import Path

PARALLEL_SUITE_PATH = Path(__file__).with_name("data") / "parallel_suite.py"


def load_parallel_suite() -> str:
    """Return the source for the parallel isolation pytest suite."""
    return PARALLEL_SUITE_PATH.read_text()


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


__all__ = ["ParallelRecord", "load_parallel_suite", "read_parallel_records"]
