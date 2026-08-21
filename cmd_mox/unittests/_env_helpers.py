"""Type-safe helpers for accessing environment paths in tests."""

from __future__ import annotations

import typing as typ

from cmd_mox.errors import MissingEnvironmentError

if typ.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox.environment import EnvironmentManager


def require_shim_dir(env: EnvironmentManager) -> Path:
    """Return ``env.shim_dir`` when initialized or raise a helpful error.

    Returns
    -------
    pathlib.Path
        The environment manager's shim directory.

    Raises
    ------
    MissingEnvironmentError
        If the environment manager has not been initialized.
    """
    if env.shim_dir is None:
        msg = "Environment manager is not initialized; shim_dir is missing"
        raise MissingEnvironmentError(msg)
    return env.shim_dir


def require_socket_path(env: EnvironmentManager) -> Path:
    """Return ``env.socket_path`` when initialized or raise a helpful error.

    Returns
    -------
    pathlib.Path
        The environment manager's IPC socket path.

    Raises
    ------
    MissingEnvironmentError
        If the environment manager has not been initialized.
    """
    if env.socket_path is None:
        msg = "Environment manager is not initialized; socket_path is missing"
        raise MissingEnvironmentError(msg)
    return env.socket_path


__all__ = ["require_shim_dir", "require_socket_path"]
