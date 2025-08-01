# ruff: noqa: D100,E402
from __future__ import annotations

"""Utilities for running commands in tests."""

import subprocess
import typing as t

if t.TYPE_CHECKING:  # pragma: no cover - types only
    import collections.abc as cabc
    from pathlib import Path


def run_cmd(
    argv: cabc.Iterable[str | Path], *, check: bool = True, **kwargs: object
) -> subprocess.CompletedProcess[str]:
    """Run *argv* capturing output.

    Parameters are forwarded to :func:`subprocess.run`. ``check`` defaults to
    ``True`` so tests fail fast on non-zero exit codes, but it can be disabled
    when callers want to inspect the result.
    """
    return subprocess.run(  # noqa: S603
        [str(a) for a in argv],
        capture_output=True,
        text=True,
        check=check,
        **kwargs,
    )
