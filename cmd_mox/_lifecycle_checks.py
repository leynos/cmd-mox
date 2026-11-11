"""Precondition checks shared by the record-replay-verify lifecycle.

These helpers read an :class:`~cmd_mox.environment.EnvironmentManager` and
raise the lifecycle errors the controller reports. They live outside the
controller so that its module stays focused on orchestration.
"""

from __future__ import annotations

from pathlib import Path

from .environment import EnvironmentManager, ensure_dir_exists, validate_env_attr
from .errors import MissingEnvironmentError


def append_lifecycle_hint(hint: str | None, message: str) -> str:
    """Append *hint* to a lifecycle error message when one is configured."""
    if hint is None:
        return message
    return f"{message}; {hint}"


def require_env_attrs(environment: EnvironmentManager | None, *attrs: str) -> None:
    """Ensure every referenced ``EnvironmentManager`` attribute exists.

    Raises
    ------
    MissingEnvironmentError
        If the environment is absent or any named attribute is unset.
    """
    if environment is None:  # pragma: no cover - defensive guard
        raise MissingEnvironmentError(MissingEnvironmentError.DEFAULT_MESSAGE)

    missing: list[str] = []
    for attr in attrs:
        error = validate_env_attr(environment, attr)
        if error is not None:
            missing.append(error)

    if missing:
        raise MissingEnvironmentError("; ".join(missing))


def validate_replay_environment(
    environment: EnvironmentManager | None,
) -> tuple[Path, Path]:
    """Return the prepared shim directory and socket path.

    Returns
    -------
    tuple[Path, Path]
        The existing shim directory and the socket path to bind.

    Raises
    ------
    MissingEnvironmentError
        If the environment is absent, the shim directory is unusable, or the
        socket path is unset.
    """
    if environment is None:
        raise MissingEnvironmentError(MissingEnvironmentError.DEFAULT_MESSAGE)

    shim_dir = ensure_dir_exists(
        environment.shim_dir,
        name="Replay shim directory",
        error_type=MissingEnvironmentError,
    )
    if environment.socket_path is None:
        msg = "Replay socket path is missing"
        raise MissingEnvironmentError(msg)
    return shim_dir, Path(environment.socket_path)


def is_environment_initialized(environment: EnvironmentManager | None) -> bool:
    """Return whether the environment has a shim directory and socket path."""
    return (
        environment is not None
        and environment.shim_dir is not None
        and environment.socket_path is not None
    )
