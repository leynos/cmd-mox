"""Utilities for executing real commands during passthrough runs."""

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

    @property
    def timeout(self) -> float:
        """Return the configured subprocess timeout."""
        return self._timeout

    def run(self, invocation: Invocation, extra_env: dict[str, str]) -> Response:
        """Execute ``invocation`` with environment overrides.

        ``extra_env`` values override the runner's original environment, while
        any keys in ``invocation.env`` take precedence over both. The returned
        :class:`Response` includes the applied overrides in ``Response.env``.

        Common failures follow POSIX-like shell conventions:

        * ``127`` - command not found
        * ``126`` - command found but not executable or execution failed
          (e.g., permission denied)
        * ``124`` - execution timed out
        """
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
        path = self._env_mgr.original_environment.get(
            "PATH", os.environ.get("PATH", "")
        )
        return resolve_command_path(command, path)

    def _prepare_environment(
        self, extra_env: dict[str, str], invocation_env: dict[str, str]
    ) -> dict[str, str]:
        """Merge the original PATH with any supplied environment variables."""
        path = self._env_mgr.original_environment.get("PATH", "")
        return prepare_environment(path, extra_env, invocation_env)

    def _execute_command(
        self, resolved_path: Path, invocation: Invocation, env: dict[str, str]
    ) -> Response:
        """Run the command and translate common errors into responses."""
        return execute_command(resolved_path, invocation, env, timeout=self._timeout)


def resolve_command_path(command: str, path: str) -> Path | Response:
    """Locate *command* within *path* returning a :class:`Path` or error response."""
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


def prepare_environment(
    original_path: str, extra_env: dict[str, str], invocation_env: dict[str, str]
) -> dict[str, str]:
    """Merge original PATH, extra env overrides, and invocation env."""
    return {"PATH": original_path} | extra_env | invocation_env


def execute_command(
    resolved_path: Path,
    invocation: Invocation,
    env: dict[str, str],
    *,
    timeout: float,
) -> Response:
    """Execute *resolved_path* using *invocation* parameters."""
    try:
        result = subprocess.run(  # noqa: S603 - shell=False prevents injection
            [str(resolved_path), *invocation.args],
            input=invocation.stdin,
            capture_output=True,
            text=True,
            env=env,
            shell=False,
            timeout=timeout,
        )
        return Response(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
        )
    except subprocess.TimeoutExpired:
        duration = int(timeout)
        return Response(
            stderr=f"{invocation.command}: timeout after {duration} seconds",
            exit_code=124,
        )
    except FileNotFoundError:
        return Response(stderr=f"{invocation.command}: not found", exit_code=127)
    except PermissionError as exc:
        return Response(stderr=f"{invocation.command}: {exc}", exit_code=126)
    except OSError as exc:
        return Response(
            stderr=f"{invocation.command}: execution failed: {exc}", exit_code=126
        )
    except Exception as exc:  # noqa: BLE001 - defensive
        return Response(
            stderr=f"{invocation.command}: unexpected error: {exc}", exit_code=126
        )
