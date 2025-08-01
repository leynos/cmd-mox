"""Shared fixtures for unit tests."""

from __future__ import annotations

import subprocess
import typing as t

import pytest


def run_subprocess(
    args: t.Sequence[str],
    **kwargs: t.Any,  # noqa: ANN401
) -> subprocess.CompletedProcess[str]:
    """Run ``subprocess.run`` with common defaults for tests."""
    return subprocess.run(  # noqa: S603
        args, capture_output=True, text=True, check=True, **kwargs
    )


@pytest.fixture(name="run")
def run_fixture() -> t.Callable[..., subprocess.CompletedProcess[str]]:
    """Provide :func:`run_subprocess` as a fixture."""
    return run_subprocess
