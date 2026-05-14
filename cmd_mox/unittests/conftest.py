"""Shared fixtures for unit tests."""

from __future__ import annotations

import collections.abc as cabc
import subprocess
import typing as typ

import pytest


def run_subprocess(
    args: cabc.Sequence[str],
    **kwargs: typ.Any,  # noqa: ANN401
) -> subprocess.CompletedProcess[str]:
    """Run ``subprocess.run`` with common defaults for tests."""
    return subprocess.run(  # noqa: S603
        args, capture_output=True, text=True, check=True, **kwargs
    )


@pytest.fixture(name="run")
def run_fixture() -> cabc.Callable[..., subprocess.CompletedProcess[str]]:
    """Provide :func:`run_subprocess` as a fixture."""
    return run_subprocess
