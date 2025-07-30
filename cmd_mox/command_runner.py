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

    def __init__(self, env_mgr: EnvironmentManager, *, timeout: float = 30.0) -> None:
        self._env_mgr = env_mgr
        self._timeout = timeout

    def run(self, invocation: Invocation, extra_env: dict[str, str]) -> Response:
        """Execute *invocation* in the original environment."""
        resolved = self._resolve_and_validate_command(invocation.command)
        if isinstance(resolved, Response):
            return resolved

        env = self._prepare_environment(extra_env, invocation.env)
        return self._execute_command(resolved, invocation, env)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _resolve_and_validate_command(self, command: str) -> Path | Response:
        """Locate *command* in PATH and ensure it is safe to execute."""
        path = self._env_mgr.original_environment.get("PATH", "")
        real = shutil.which(command, path=path)
        if real is None:
            return Response(stderr=f"{command}: not found", exit_code=127)

        resolved = Path(real)
        if not resolved.is_absolute():
            return Response(stderr=f"{command}: invalid executable path", exit_code=126)

        resolved = resolved.resolve()
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            return Response(stderr=f"{command}: not executable", exit_code=126)

        return resolved

    def _prepare_environment(
        self, extra_env: dict[str, str], invocation_env: dict[str, str]
    ) -> dict[str, str]:
        """Merge the original PATH with any supplied environment variables."""
        path = self._env_mgr.original_environment.get("PATH", "")
        return {"PATH": path} | extra_env | invocation_env

    def _execute_command(
        self, resolved_path: Path, invocation: Invocation, env: dict[str, str]
    ) -> Response:
        """Run the command and translate common errors into responses."""
        try:
            result = subprocess.run(  # noqa: S603 - shell=False with list args prevents injection
                [str(resolved_path), *invocation.args],
                input=invocation.stdin,
                capture_output=True,
                text=True,
                env=env,
                shell=False,
                timeout=self._timeout,
            )
            return Response(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
            )
        except subprocess.TimeoutExpired:
            duration = int(self._timeout)
            return Response(
                stderr=f"{invocation.command}: timeout after {duration} seconds",
                exit_code=124,
            )
        except (FileNotFoundError, PermissionError) as e:
            return Response(stderr=f"{invocation.command}: {e}", exit_code=126)
        except OSError as e:
            return Response(
                stderr=f"{invocation.command}: execution failed: {e}", exit_code=126
            )
        except Exception as e:  # noqa: BLE001 - broad catch for safety
            return Response(
                stderr=f"{invocation.command}: unexpected error: {e}",
                exit_code=126,
            )
