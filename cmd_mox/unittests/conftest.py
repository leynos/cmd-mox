"""Shared fixtures for unit tests."""

from __future__ import annotations

import subprocess
import typing as typ

import pytest

from cmd_mox.ipc import server as unix_server
from cmd_mox.ipc._unix_server_base import resolve_unix_server_base

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
def unix_server_base_cache() -> cabc.Iterator[None]:
    """Clear both lazy server caches around patched platform cases."""
    resolve_unix_server_base.cache_clear()
    unix_server._inner_server_cls.cache_clear()
    yield
    unix_server._inner_server_cls.cache_clear()
    resolve_unix_server_base.cache_clear()
