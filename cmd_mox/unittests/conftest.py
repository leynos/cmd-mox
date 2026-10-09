"""Shared fixtures for unit tests."""

from __future__ import annotations

import os
import subprocess
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def run_subprocess(
    args: cabc.Sequence[str],
    **kwargs: typ.Any,  # ruff: ignore[any-type] - forwarded verbatim to subprocess.run, whose keyword arguments are heterogeneous
) -> subprocess.CompletedProcess[str]:
    """Run ``subprocess.run`` with common defaults for tests.

    Returns
    -------
    subprocess.CompletedProcess[str]
        The completed process returned by ``subprocess.run``.
    """
    return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - the test executes a command path prepared by the test harness
        args, capture_output=True, text=True, check=True, **kwargs
    )


@pytest.fixture(name="run")
def run_fixture() -> cabc.Callable[..., subprocess.CompletedProcess[str]]:
    """Provide :func:`run_subprocess` as a fixture.

    Returns
    -------
    collections.abc.Callable
        The subprocess helper used by tests.
    """
    return run_subprocess


@pytest.fixture
def stdin_pipe_descriptor() -> cabc.Iterator[int]:
    """Yield an owned pipe descriptor for stdin polling tests.

    Yields
    ------
    int
        The read end of the pipe.
    """
    read_descriptor, write_descriptor = os.pipe()
    try:
        yield read_descriptor
    finally:
        os.close(read_descriptor)
        os.close(write_descriptor)


@pytest.fixture
def stdin_pipe_descriptor_at_eof() -> cabc.Iterator[int]:
    """Yield a pipe read descriptor after closing its writer.

    Yields
    ------
    int
        The read end of the pipe, which returns EOF immediately.
    """
    read_descriptor, write_descriptor = os.pipe()
    os.close(write_descriptor)
    try:
        yield read_descriptor
    finally:
        os.close(read_descriptor)
