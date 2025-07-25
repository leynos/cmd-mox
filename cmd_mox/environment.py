"""Environment manipulation helpers for CmdMox."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import typing as t
from pathlib import Path

_active_manager: EnvironmentManager | None = None

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types

CMOX_IPC_SOCKET_ENV = "CMOX_IPC_SOCKET"
CMOX_IPC_TIMEOUT_ENV = "CMOX_IPC_TIMEOUT"  # server/shim communication timeout


class EnvironmentManager:
    """Manage temporary environment modifications for CmdMox.

    The manager is not re-entrant; nested usage is unsupported and will raise
    ``RuntimeError``. This keeps the restore logic simple and prevents
    inadvertent environment leakage.
    """

    def __init__(self, *, prefix: str = "cmdmox-") -> None:
        self._orig_env: dict[str, str] | None = None
        self.shim_dir: Path | None = None
        self.socket_path: Path | None = None
        self._created_dir: Path | None = None
        self._prefix = prefix

    def __enter__(self) -> EnvironmentManager:
        """Set up the temporary environment."""
        global _active_manager
        if self._orig_env is not None or _active_manager is not None:
            msg = "EnvironmentManager cannot be nested"
            raise RuntimeError(msg)
        _active_manager = self
        self._orig_env = os.environ.copy()
        self.shim_dir = Path(tempfile.mkdtemp(prefix=self._prefix))
        self._created_dir = self.shim_dir
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
            global _active_manager
            _active_manager = None
            if (
                self._created_dir
                and self.shim_dir
                and self.shim_dir == self._created_dir
                and self.shim_dir.exists()
            ):
                shutil.rmtree(self.shim_dir, ignore_errors=True)
            self._created_dir = None


@contextlib.contextmanager
def temporary_env(mapping: dict[str, str]) -> t.Iterator[None]:
    """Temporarily apply environment variables from *mapping*."""
    saved: dict[str, str | None] = {k: os.environ.get(k) for k in mapping}
    os.environ.update(mapping)
    try:
        yield
    finally:
        for key, val in saved.items():
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val
