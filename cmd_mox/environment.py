"""Environment manipulation helpers for CmdMox."""

from __future__ import annotations

import os
import shutil
import tempfile
import typing as t
from pathlib import Path

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types

CMOX_IPC_SOCKET_ENV = "CMOX_IPC_SOCKET"


class EnvironmentManager:
    """Manage temporary environment modifications for CmdMox.

    The manager is not re-entrant; nested usage is unsupported and will raise
    ``RuntimeError``. This keeps the restore logic simple and prevents
    inadvertent environment leakage.
    """

    def __init__(self) -> None:
        self._orig_env: dict[str, str] | None = None
        self.shim_dir: Path | None = None
        self.socket_path: Path | None = None

    def __enter__(self) -> EnvironmentManager:
        """Set up the temporary environment."""
        if self._orig_env is not None:
            msg = "EnvironmentManager cannot be nested"
            raise RuntimeError(msg)
        self._orig_env = os.environ.copy()
        self.shim_dir = Path(tempfile.mkdtemp(prefix="cmdmox-"))
        os.environ["PATH"] = os.pathsep.join(
            [str(self.shim_dir), self._orig_env.get("PATH", "")]
        )
        self.socket_path = self.shim_dir / "ipc.sock"
        os.environ[CMOX_IPC_SOCKET_ENV] = str(self.socket_path)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        """Restore the original environment and clean up."""
        try:
            if self._orig_env is not None:
                orig_env = self._orig_env
                for key in list(os.environ):
                    if key not in orig_env:
                        os.environ.pop(key, None)
                    elif os.environ[key] != orig_env[key]:
                        os.environ[key] = orig_env[key]
                for key, value in orig_env.items():
                    if key not in os.environ:
                        os.environ[key] = value
                self._orig_env = None
        finally:
            if self.shim_dir and self.shim_dir.exists():
                shutil.rmtree(self.shim_dir, ignore_errors=True)
