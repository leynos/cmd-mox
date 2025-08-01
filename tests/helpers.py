# ruff: noqa: D100,E402
from __future__ import annotations

"""Utilities for running commands in tests."""

import subprocess
import typing as t

if t.TYPE_CHECKING:  # pragma: no cover - types only
    import collections.abc as cabc
    from pathlib import Path


def run_cmd(
    argv: cabc.Iterable[str | Path], **kwargs: object
) -> subprocess.CompletedProcess[str]:
    """Run *argv* capturing output and raising on failure."""
    return subprocess.run(  # noqa: S603
        [str(a) for a in argv],
        capture_output=True,
        text=True,
        check=True,
        **kwargs,
    )
