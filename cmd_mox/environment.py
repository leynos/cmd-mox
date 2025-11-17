"""Environment manipulation helpers for CmdMox."""

from __future__ import annotations

import contextlib
import functools
import logging
import ntpath
import os
import shutil
import tempfile
import threading
import time
import typing as t
from pathlib import Path

from ._validators import validate_positive_finite_timeout

IS_WINDOWS = os.name == "nt"
_MAX_PATH_THRESHOLD: t.Final[int] = 240

logger = logging.getLogger(__name__)

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types

CMOX_IPC_SOCKET_ENV = "CMOX_IPC_SOCKET"
CMOX_IPC_TIMEOUT_ENV = "CMOX_IPC_TIMEOUT"  # server/shim communication timeout
CMOX_REAL_COMMAND_ENV_PREFIX = "CMOX_REAL_COMMAND_"

_UNSET_TIMEOUT = object()


def _normalise_path_string(path: str) -> str:
    """Return a canonical representation for filesystem comparisons."""
    module = ntpath if IS_WINDOWS else os.path
    normalised = module.normpath(path)
    if IS_WINDOWS:
        normalised = module.normcase(normalised)
    return normalised


def _path_identity(path: Path | None) -> str | None:
    """Return a comparable representation of *path*, or ``None`` if unset."""
    if path is None:
        return None
    return _normalise_path_string(os.fspath(path))


def _should_shorten_path(raw_path: Path) -> bool:
    """Return True if *raw_path* risks exceeding the Windows MAX_PATH limit."""
    if not IS_WINDOWS:
        return False
    return len(os.fspath(raw_path)) >= _MAX_PATH_THRESHOLD


def _get_short_path(path: Path) -> Path | None:
    """Return the short (8.3) variant for *path*, or ``None`` if unavailable."""
    if not IS_WINDOWS:
        return None

    # Importing ctypes lazily keeps non-Windows interpreters free of win32
    # specifics and avoids attribute errors in environments without WinDLL.
    import ctypes
    from ctypes import wintypes

    ctypes_module = t.cast("t.Any", ctypes)
    kernel32 = ctypes_module.WinDLL("kernel32", use_last_error=True)
    get_short_path_name = kernel32.GetShortPathNameW
    get_short_path_name.argtypes = (  # type: ignore[attr-defined]
        wintypes.LPCWSTR,
        wintypes.LPWSTR,
        wintypes.DWORD,
    )
    get_short_path_name.restype = wintypes.DWORD

    raw = os.fspath(path)
    # Provide an initial buffer large enough for typical conversions while
    # still growing dynamically for pathological cases.
    buffer_len = max(len(raw) + 1, 260)

    while True:
        buffer = ctypes.create_unicode_buffer(buffer_len)
        result = get_short_path_name(raw, buffer, buffer_len)
        if result == 0:
            error = ctypes_module.get_last_error()
            # Error codes of 0/2/3 imply the filesystem declined to provide a
            # short path (either because it does not exist yet or the volume
            # disabled 8.3 aliases). Falling back to the original path keeps
            # CmdMox functional even without short-path support.
            if error in (0, 2, 3):
                return None
            raise OSError(ctypes_module.FormatError(error))
        if result >= buffer_len:
            buffer_len = result + 1
            continue
        return Path(buffer.value)


def _maybe_shorten_windows_path(path: Path) -> Path:
    """Return a MAX_PATH-safe variant of *path* when running on Windows."""
    if not IS_WINDOWS or not _should_shorten_path(path):
        return path

    short_path = _get_short_path(path)
    return short_path if short_path is not None else path


def _restore_env(orig_env: dict[str, str]) -> None:
    """Reset ``os.environ`` to the snapshot stored in ``orig_env``."""
    os.environ.clear()
    os.environ.update(orig_env)


class RobustRmtreeError(OSError):
    """Raised when :func:`_robust_rmtree` exhausts all removal attempts."""

    def __init__(
        self, path: Path, attempts: int, last_exception: Exception | None
    ) -> None:
        msg = f"Failed to remove {path} after {attempts} attempts"
        super().__init__(msg)
        self.path = path
        self.attempts = attempts
        self.last_exception = last_exception


def _robust_rmtree(path: Path, max_attempts: int = 4, retry_delay: float = 0.1) -> None:
    """Remove directory tree with retries and better error handling."""
    if not path.exists():
        return
    _retry_removal(path, max_attempts, retry_delay)


def _retry_removal(path: Path, attempts: int, retry_delay: float) -> None:
    """Attempt to remove *path* up to ``attempts`` times."""
    last_exception: Exception | None = None
    for attempt in range(attempts):
        try:
            raise_on_error = attempt == attempts - 1
            if _attempt_single_removal(path, raise_on_error=raise_on_error):
                return
        except OSError as exc:
            last_exception = exc
        if attempt < attempts - 1:
            _log_retry_attempt(attempt, path, retry_delay)
            time.sleep(retry_delay)
        else:
            _handle_final_failure(path, attempts, last_exception)


def _attempt_single_removal(path: Path, *, raise_on_error: bool) -> bool:
    """Try removing *path* once; return ``True`` on success."""
    try:
        _fix_windows_permissions(path)
        shutil.rmtree(path)
        logger.debug("Successfully removed temporary directory: %s", path)
    except OSError:
        if raise_on_error:
            raise
        return False
    else:
        return True


def _fix_windows_permissions(path: Path) -> None:
    """Ensure all files under *path* are writable on Windows."""
    if not IS_WINDOWS:
        return
    for root, dirs, files in os.walk(path):
        for name in files:
            p = Path(root) / name
            if p.exists() and not p.is_symlink():
                p.chmod(0o777)
        for name in dirs:
            p = Path(root) / name
            if p.exists() and not p.is_symlink():
                p.chmod(0o777)


def _ensure_windows_pathext(original: dict[str, str]) -> None:
    """Guarantee that ``.CMD`` entries are available in ``PATHEXT`` on Windows."""
    if not IS_WINDOWS:
        return

    pathext = original.get("PATHEXT", "")
    if not pathext:
        os.environ["PATHEXT"] = os.pathsep.join([".COM", ".EXE", ".BAT", ".CMD"])
        return

    parts = [part.strip() for part in pathext.split(os.pathsep) if part.strip()]
    seen = {part.upper() for part in parts}
    if ".CMD" not in seen:
        parts.append(".CMD")
    os.environ["PATHEXT"] = os.pathsep.join(parts)


def _log_retry_attempt(attempt: int, path: Path, delay: float) -> None:
    """Log that a removal attempt failed and will retry."""
    logger.debug(
        "Attempt %d to remove %s failed. Retrying in %.1fs...",
        attempt + 1,
        path,
        delay,
    )


def _handle_final_failure(
    path: Path, attempts: int, last_exception: Exception | None
) -> None:
    """Log and raise after exhausting all retries."""
    logger.warning(
        "Failed to remove temporary directory %s after %d attempts",
        path,
        attempts,
    )
    raise RobustRmtreeError(path, attempts, last_exception) from last_exception


CleanupError = tuple[str, BaseException]

P = t.ParamSpec("P")
R = t.TypeVar("R")


def _collect_os_error(
    message: str,
) -> t.Callable[
    [
        t.Callable[
            t.Concatenate[EnvironmentManager, list[CleanupError], P],
            R,
        ]
    ],
    t.Callable[t.Concatenate[EnvironmentManager, list[CleanupError], P], None],
]:
    """Return a decorator that records ``OSError``s in ``cleanup_errors``.

    The decorated function is expected to take ``(self, cleanup_errors)`` and
    should raise ``OSError`` on failure. Any such exception is captured and the
    formatted message appended to ``cleanup_errors``.
    """

    def decorator(
        func: t.Callable[t.Concatenate[EnvironmentManager, list[CleanupError], P], R],
    ) -> t.Callable[t.Concatenate[EnvironmentManager, list[CleanupError], P], None]:
        @functools.wraps(func)
        def wrapper(
            self: EnvironmentManager,
            cleanup_errors: list[CleanupError],
            *args: P.args,
            **kwargs: P.kwargs,
        ) -> None:
            try:
                func(self, cleanup_errors, *args, **kwargs)
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

    # Track the active manager per thread to avoid cross-thread interference.
    _state: t.ClassVar[threading.local] = threading.local()

    @classmethod
    def get_active_manager(cls) -> EnvironmentManager | None:
        """Return the active manager for the current thread, if any."""
        return getattr(cls._state, "active_manager", None)

    @classmethod
    def reset_active_manager(cls) -> None:
        """Clear any active manager for the current thread."""
        cls._state.active_manager = None

    @classmethod
    def _set_active_manager(cls, mgr: EnvironmentManager) -> None:
        """Record *mgr* as active for the current thread."""
        cls._state.active_manager = mgr

    def __init__(self, *, prefix: str = "cmdmox-") -> None:
        self._orig_env: dict[str, str] | None = None
        self.shim_dir: Path | None = None
        self.socket_path: Path | None = None
        self.ipc_timeout: float | None = None
        self._created_dir: Path | None = None
        self._prefix = prefix

    def __enter__(self) -> EnvironmentManager:
        """Set up the temporary environment."""
        cls = type(self)
        if self._orig_env is not None or cls.get_active_manager() is not None:
            msg = "EnvironmentManager cannot be nested"
            raise RuntimeError(msg)
        cls._set_active_manager(self)
        self._orig_env = os.environ.copy()
        shim_dir = Path(tempfile.mkdtemp(prefix=self._prefix))
        shim_dir = _maybe_shorten_windows_path(shim_dir)
        self.shim_dir = shim_dir
        self._created_dir = self.shim_dir
        os.environ["PATH"] = os.pathsep.join(
            [str(self.shim_dir), self._orig_env.get("PATH", "")]
        )
        _ensure_windows_pathext(self._orig_env)
        self.socket_path = self.shim_dir / "ipc.sock"
        self.export_ipc_environment()
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
        self.ipc_timeout = None

    @_collect_os_error("Environment restoration failed")
    def _restore_original_environment(
        self, _cleanup_errors: list[CleanupError]
    ) -> None:
        """Return the process environment to its original state."""
        if self._orig_env is not None:
            _restore_env(self._orig_env)
            if IS_WINDOWS:
                original = self._orig_env.get("PATHEXT")
                restored = os.environ.get("PATHEXT")
                if restored != original:
                    msg = "PATHEXT was not restored after environment teardown"
                    raise AssertionError(msg)
            self._orig_env = None

    def _reset_global_state(self) -> None:
        """Reset thread-local state tracking the active manager."""
        type(self).reset_active_manager()

    def _should_skip_directory_removal(self) -> bool:
        """Return ``True`` if no matching temporary directory remains."""
        shim = self.shim_dir
        created = self._created_dir
        if created is None or shim is None:
            return True
        if _path_identity(created) != _path_identity(shim):
            return True
        return not shim.exists()

    def _has_mismatched_directories(self) -> bool:
        """Check if the created directory differs from the current shim directory."""
        created = self._created_dir
        shim = self.shim_dir
        if created is None or shim is None:
            return False
        return _path_identity(created) != _path_identity(shim)

    @_collect_os_error("Directory cleanup failed")
    def _cleanup_temporary_directory(self, _cleanup_errors: list[CleanupError]) -> None:
        """Remove the temporary directory created by ``__enter__``."""
        if self._should_skip_directory_removal():
            if self._has_mismatched_directories():
                logger.warning(
                    "Skipping cleanup for original temporary directory %s because "
                    "shim_dir now points to %s; leftover directories may remain.",
                    self._created_dir,
                    self.shim_dir,
                )
                # Once ownership of the shim directory diverges from the original
                # temporary directory, the manager no longer tracks the replacement.
                # Resetting ``shim_dir`` avoids stale references to directories we did
                # not create and therefore should not manage.
                self.shim_dir = None
            self._created_dir = None
            return

        shim = t.cast("Path", self.shim_dir)  # helper ensures this is a Path
        try:
            _robust_rmtree(shim)
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

    def _validate_timeout(self, timeout: float) -> None:
        """Validate that *timeout* is a positive finite number.

        Raise ``ValueError`` if the provided value is not positive and finite.
        """
        validate_positive_finite_timeout(timeout)

    def _resolve_effective_timeout(self, timeout: float | object) -> float | None:
        """Return the timeout value that should be exported.

        The helper isolates the branching necessary to honour explicit
        overrides, fall back to the previously configured value, and surface
        invalid types consistently with other validation paths.
        """
        if timeout is _UNSET_TIMEOUT:
            return self.ipc_timeout

        if timeout is None:
            msg = "timeout must be a real number"
            raise TypeError(msg)

        override = t.cast("float", timeout)
        self._validate_timeout(override)
        self.ipc_timeout = override
        return override

    def export_ipc_environment(
        self, *, timeout: float | object = _UNSET_TIMEOUT
    ) -> None:
        """Expose IPC configuration variables for active shims."""
        if self.socket_path is None:
            msg = "Cannot export IPC settings before entering the environment"
            raise RuntimeError(msg)

        os.environ[CMOX_IPC_SOCKET_ENV] = str(self.socket_path)

        effective_timeout = self._resolve_effective_timeout(timeout)
        if effective_timeout is None:
            os.environ.pop(CMOX_IPC_TIMEOUT_ENV, None)
            return

        os.environ[CMOX_IPC_TIMEOUT_ENV] = str(effective_timeout)


@contextlib.contextmanager
def temporary_env(mapping: dict[str, str]) -> t.Iterator[None]:
    """Temporarily apply environment variables from *mapping*."""
    orig_env = os.environ.copy()
    os.environ.update(mapping)
    try:
        yield
    finally:
        _restore_env(orig_env)
