# ruff: noqa: S101
"""pytest-bdd steps that manage shim lifecycle and cleanup."""

from __future__ import annotations

import typing as t
from pathlib import Path

from pytest_bdd import parsers, then, when

from cmd_mox.environment import EnvironmentManager

ReplayInterruptionMapping = t.Mapping[str, Path | EnvironmentManager | None]

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from cmd_mox.controller import CmdMox


def _require_replay_shim_dir(mox: CmdMox) -> Path:
    """Return the shim directory when replay is active, asserting availability."""
    env = mox.environment
    if env is None or env.shim_dir is None:
        msg = "Replay environment is unavailable"
        raise AssertionError(msg)
    return Path(env.shim_dir)


@when(parsers.cfparse('the shim for "{cmd}" is broken'))
def break_shim_symlink(mox: CmdMox, cmd: str) -> None:
    """Replace the shim with a dangling symlink to simulate corruption."""
    shim_dir = _require_replay_shim_dir(mox)
    shim_path = shim_dir / cmd
    missing_target = shim_path.with_name(f"{cmd}-missing-target")
    shim_path.unlink(missing_ok=True)
    shim_path.symlink_to(missing_target)
    assert shim_path.is_symlink()
    assert not shim_path.exists()


@when(parsers.cfparse('I register the command "{cmd}" during replay'))
def register_command_during_replay(mox: CmdMox, cmd: str) -> None:
    """Re-register *cmd* so CmdMox can repair its shim."""
    _require_replay_shim_dir(mox)
    mox.register_command(cmd)


@then("the shim directory should be cleaned up after interruption")
def check_shim_dir_cleaned(
    replay_interruption_state: ReplayInterruptionMapping,
) -> None:
    """Assert the temporary shim directory no longer exists."""
    shim_dir = t.cast("Path", replay_interruption_state["shim_dir"])
    assert not shim_dir.exists()
    assert replay_interruption_state["manager_active"] is None


@then("the IPC socket should be cleaned up after interruption")
def check_socket_cleaned(
    replay_interruption_state: ReplayInterruptionMapping,
) -> None:
    """Assert the IPC socket path no longer exists."""
    socket_path = t.cast("Path", replay_interruption_state["socket_path"])
    assert not socket_path.exists()
