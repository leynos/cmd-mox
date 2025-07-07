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
    """Manage temporary environment modifications for CmdMox."""

    def __init__(self) -> None:
        self._orig_env: dict[str, str] | None = None
        self.shim_dir: Path | None = None
        self.socket_path: Path | None = None

    def __enter__(self) -> EnvironmentManager:
        """Set up the temporary environment."""
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
                os.environ.clear()
                os.environ.update(self._orig_env)
        finally:
            if self.shim_dir and self.shim_dir.exists():
                shutil.rmtree(self.shim_dir, ignore_errors=True)
