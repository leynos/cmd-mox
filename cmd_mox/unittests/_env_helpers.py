"""Type-safe helpers for accessing environment paths in tests."""

from __future__ import annotations

import typing as t

from cmd_mox.errors import MissingEnvironmentError

if t.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox.environment import EnvironmentManager


def require_shim_dir(env: EnvironmentManager) -> Path:
    """Return ``env.shim_dir`` when initialised or raise a helpful error."""
    if env.shim_dir is None:
        msg = "Environment manager is not initialised; shim_dir is missing"
        raise MissingEnvironmentError(msg)
    return env.shim_dir


def require_socket_path(env: EnvironmentManager) -> Path:
    """Return ``env.socket_path`` when initialised or raise a helpful error."""
    if env.socket_path is None:
        msg = "Environment manager is not initialised; socket_path is missing"
        raise MissingEnvironmentError(msg)
    return env.socket_path


__all__ = ["require_shim_dir", "require_socket_path"]
