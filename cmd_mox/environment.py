"""Environment manipulation helpers for CmdMox."""

from __future__ import annotations

import contextlib
import functools
import logging
import os
import shutil
import tempfile
import time
import typing as t
from pathlib import Path

_active_manager: EnvironmentManager | None = None  # type: ignore[name-defined]

logger = logging.getLogger(__name__)

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types

CMOX_IPC_SOCKET_ENV = "CMOX_IPC_SOCKET"
CMOX_IPC_TIMEOUT_ENV = "CMOX_IPC_TIMEOUT"  # server/shim communication timeout


def _restore_env(orig_env: dict[str, str]) -> None:
    """Reset ``os.environ`` to the snapshot stored in ``orig_env``."""
    os.environ.clear()
    os.environ.update(orig_env)


def _robust_rmtree(path: Path, max_retries: int = 3, retry_delay: float = 0.1) -> None:
    """Remove directory tree with retries and better error handling.

    Parameters
    ----------
    path
        Directory path to remove
    max_retries
        Maximum number of retry attempts
    retry_delay
        Delay between retries in seconds
    """
    if not path.exists():
        return

    def _attempt_removal(*, raise_on_error: bool = False) -> bool:
        """Attempt to remove the directory tree. Return True on success."""
        try:
            # First try to remove read-only attributes if on Windows
            if os.name == "nt":
                for root, dirs, files in os.walk(path):
                    for file in files:
                        file_path = Path(root) / file
                        if file_path.exists():
                            file_path.chmod(0o777)
                    for dir_name in dirs:
                        dir_path = Path(root) / dir_name
                        if dir_path.exists():
                            dir_path.chmod(0o777)

            shutil.rmtree(path)
            logger.debug("Successfully removed temporary directory: %s", path)
        except OSError:
            if raise_on_error:
                raise  # Re-raise the exception
            return False
        else:
            return True

    last_exception: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            # On the final attempt, re-raise any exceptions
            raise_on_error = attempt == max_retries
            if _attempt_removal(raise_on_error=raise_on_error):
                return
        except OSError as e:
            last_exception = e

        if attempt < max_retries:
            logger.debug(
                "Attempt %d to remove %s failed. Retrying in %.1fs...",
                attempt + 1,
                path,
                retry_delay,
            )
            time.sleep(retry_delay)
        else:
            logger.warning(
                "Failed to remove temporary directory %s after %d attempts",
                path,
                max_retries + 1,
            )
            # Re-raise the last exception we caught
            if last_exception is not None:
                raise last_exception
            msg = f"Failed to remove {path} after {max_retries + 1} attempts"
            raise OSError(msg)


CleanupError = tuple[str, BaseException]


def _collect_os_error(
    message: str,
) -> t.Callable[
    [t.Callable[[EnvironmentManager, list[CleanupError]], t.Any]],
    t.Callable[[EnvironmentManager, list[CleanupError]], None],
]:
    """Return a decorator that records ``OSError``s in ``cleanup_errors``.

    The decorated function is expected to take ``(self, cleanup_errors)`` and
    should raise ``OSError`` on failure. Any such exception is captured and the
    formatted message appended to ``cleanup_errors``.
    """

    def decorator(
        func: t.Callable[[EnvironmentManager, list[CleanupError]], t.Any],
    ) -> t.Callable[[EnvironmentManager, list[CleanupError]], None]:
        @functools.wraps(func)
        def wrapper(
            self: EnvironmentManager, cleanup_errors: list[CleanupError]
        ) -> None:
            try:
                func(self, cleanup_errors)
            except OSError as e:  # pragma: no cover - exercised via tests
                cleanup_errors.append((f"{message}: {e}", e))

        return wrapper

    return decorator


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
        cleanup_errors: list[CleanupError] = []

        self._restore_original_environment(cleanup_errors)
        self._reset_global_state()
        self._cleanup_temporary_directory(cleanup_errors)
        self._handle_cleanup_errors(cleanup_errors, exc_type)

    @_collect_os_error("Environment restoration failed")
    def _restore_original_environment(
        self, _cleanup_errors: list[CleanupError]
    ) -> None:
        """Return the process environment to its original state."""
        if self._orig_env is not None:
            _restore_env(self._orig_env)
            self._orig_env = None

    def _reset_global_state(self) -> None:
        """Reset module-level state tracking the active manager."""
        global _active_manager
        _active_manager = None

    @_collect_os_error("Directory cleanup failed")
    def _cleanup_temporary_directory(self, _cleanup_errors: list[CleanupError]) -> None:
        """Remove the temporary directory created by ``__enter__``."""
        try:
            if (
                self._created_dir
                and self.shim_dir
                and self.shim_dir == self._created_dir
                and self.shim_dir.exists()
            ):
                _robust_rmtree(self.shim_dir)
        finally:
            self._created_dir = None

    def _handle_cleanup_errors(
        self,
        cleanup_errors: list[CleanupError],
        exc_type: type[BaseException] | None,
    ) -> None:
        """Log and potentially raise aggregated cleanup errors."""
        if cleanup_errors:
            messages = [msg for msg, _ in cleanup_errors]
            error_msg = "; ".join(messages)
            logger.error("EnvironmentManager cleanup encountered errors: %s", error_msg)
            # Only raise if we're not already handling an exception
            if exc_type is None:
                primary_exc = cleanup_errors[0][1]
                msg = f"Cleanup failed: {error_msg}"
                raise RuntimeError(msg) from primary_exc

    @property
    def original_environment(self) -> dict[str, str]:
        """Return the unmodified environment prior to ``__enter__``."""
        return self._orig_env or {}


@contextlib.contextmanager
def temporary_env(mapping: dict[str, str]) -> t.Iterator[None]:
    """Temporarily apply environment variables from *mapping*."""
    orig_env = os.environ.copy()
    os.environ.update(mapping)
    try:
        yield
    finally:
        _restore_env(orig_env)
