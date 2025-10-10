"""Global test configuration and shared fixtures."""

from __future__ import annotations

import socket
import tempfile
import typing as t
from pathlib import Path

import pytest

import cmd_mox.environment

_UNIX_SOCKETS_SUPPORTED: bool | None = None


def _can_bind_unix_socket() -> bool:
    """Return ``True`` when the platform allows binding Unix domain sockets."""
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            sock_path = Path(tmp_dir) / "probe.sock"
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                sock.bind(str(sock_path))
            except PermissionError:
                return False
            finally:
                sock.close()
                if sock_path.exists():
                    sock_path.unlink()
    except OSError:
        return False
    else:
        return True


def _unix_sockets_supported() -> bool:
    global _UNIX_SOCKETS_SUPPORTED
    if _UNIX_SOCKETS_SUPPORTED is None:
        _UNIX_SOCKETS_SUPPORTED = _can_bind_unix_socket()
    return _UNIX_SOCKETS_SUPPORTED


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers and cache platform capability checks."""
    config.addinivalue_line(
        "markers",
        "requires_unix_sockets: mark test as requiring Unix domain socket support",
    )
    _unix_sockets_supported()


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip tests needing Unix sockets when the platform disallows them."""
    if _unix_sockets_supported():
        return
    skip = pytest.mark.skip(
        reason="Unix domain sockets are not permitted in this environment"
    )
    for item in items:
        if "requires_unix_sockets" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def reset_environment_manager_state() -> t.Generator[None, None, None]:
    """Ensure clean state for ``EnvironmentManager`` between tests."""
    cmd_mox.environment.EnvironmentManager.reset_active_manager()
    yield
    cmd_mox.environment.EnvironmentManager.reset_active_manager()
