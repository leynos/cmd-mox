"""Run real commands for passthrough spies."""

from __future__ import annotations

import os
import shutil
import subprocess
import typing as t
from pathlib import Path

from .ipc import Invocation, Response

if t.TYPE_CHECKING:  # pragma: no cover - used for type hints
    from .environment import EnvironmentManager


class CommandRunner:
    """Run commands using the original system environment."""

    def __init__(self, env_mgr: EnvironmentManager) -> None:
        self._env_mgr = env_mgr

    def run(self, invocation: Invocation, extra_env: dict[str, str]) -> Response:
        """Execute *invocation* in the original environment."""
        orig_env = self._env_mgr.original_environment
        path = orig_env.get("PATH", "")
        real = shutil.which(invocation.command, path=path)
        if real is None:
            return Response(stderr=f"{invocation.command}: not found", exit_code=127)

        resolved = Path(real).resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return Response(
                stderr=f"{invocation.command}: not executable", exit_code=126
            )

        env = {"PATH": path}
        env.update(extra_env)
        env.update(invocation.env)

        try:
            result = subprocess.run(  # noqa: S603 - invocation args are controlled
                [str(resolved), *invocation.args],
                input=invocation.stdin,
                capture_output=True,
                text=True,
                env=env,
                shell=False,
                timeout=30,
            )
            return Response(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            return Response(
                stderr=f"{invocation.command}: timeout after 30 seconds",
                exit_code=124,
            )
        except (FileNotFoundError, PermissionError) as e:
            return Response(stderr=f"{invocation.command}: {e}", exit_code=126)
        except OSError as e:
            return Response(
                stderr=f"{invocation.command}: execution failed: {e}",
                exit_code=126,
            )
