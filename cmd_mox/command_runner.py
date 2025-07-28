"""Run real commands for passthrough spies."""

from __future__ import annotations

import os
import shutil
import subprocess
import typing as t

from .ipc import Invocation, Response

if t.TYPE_CHECKING:  # pragma: no cover - used for type hints
    from .environment import EnvironmentManager


class CommandRunner:
    """Run commands using the original system environment."""

    def __init__(self, env_mgr: EnvironmentManager) -> None:
        self._env_mgr = env_mgr

    def run(self, invocation: Invocation, extra_env: dict[str, str]) -> Response:
        """Execute *invocation* in the original environment."""
        orig_env = self._env_mgr._orig_env or {}
        path = orig_env.get("PATH", os.environ.get("PATH", ""))
        real = shutil.which(invocation.command, path=path)
        if real is None:
            return Response(stderr=f"{invocation.command}: not found", exit_code=127)

        env = {**invocation.env, "PATH": path, **extra_env}
        result = subprocess.run(  # noqa: S603 - invocation args are controlled
            [real, *invocation.args],
            input=invocation.stdin,
            capture_output=True,
            text=True,
            env=env,
            shell=False,
        )
        return Response(
            stdout=result.stdout, stderr=result.stderr, exit_code=result.returncode
        )
