"""Reusable filesystem cleanup helpers with retry/backoff policies."""

from __future__ import annotations

import dataclasses as dc
import logging
import os
import shutil
import time
import typing as t
from pathlib import Path

from . import _path_utils as path_utils

_logger = logging.getLogger(__name__)


@dc.dataclass(frozen=True, slots=True)
class RetryConfig:
    """Configuration for retry loops."""

    max_attempts: int
    retry_delay: float

    def __post_init__(self) -> None:
        """Validate retry configuration values."""
        if self.max_attempts < 1:
            msg = "max_attempts must be >= 1"
            raise ValueError(msg)
        if self.retry_delay < 0:
            msg = "retry_delay must be >= 0"
            raise ValueError(msg)


DEFAULT_UNLINK_RETRY = RetryConfig(max_attempts=3, retry_delay=0.5)
DEFAULT_RMTREE_RETRY = RetryConfig(max_attempts=4, retry_delay=0.1)


class RobustRmtreeError(OSError):
    """Raised when :func:`robust_rmtree` exhausts all removal attempts."""

    def __init__(
        self, path: Path, attempts: int, last_exception: Exception | None
    ) -> None:
        msg = f"Failed to remove {path} after {attempts} attempts"
        super().__init__(msg)
        self.path = path
        self.attempts = attempts
        self.last_exception = last_exception


def _fix_windows_permissions(path: Path) -> None:
    """Ensure all files under *path* are writable on Windows before deletion."""
    if not path_utils.IS_WINDOWS:
        return

    for root, dirs, files in os.walk(path):
        root_path = Path(root)
        for name in (*files, *dirs):
            candidate = root_path / name
            if candidate.exists() and not candidate.is_symlink():
                candidate.chmod(0o777)


def _path_is_missing(path: Path, exc: OSError) -> bool:
    """Check if the path is missing (either exception indicates or actual check)."""
    return isinstance(exc, FileNotFoundError) or not path.exists()


def _handle_rmtree_final_failure(
    path: Path, attempts: int, exc: OSError, logger: logging.Logger
) -> t.NoReturn:
    """Handle final rmtree failure by logging and raising RobustRmtreeError."""
    logger.warning(
        "Failed to remove temporary directory %s after %d attempts",
        path,
        attempts,
    )
    raise RobustRmtreeError(path, attempts, exc) from exc


def _log_rmtree_success(path: Path, logger: logging.Logger) -> None:
    """Log successful directory removal."""
    logger.debug("Successfully removed temporary directory: %s", path)


def _handle_unlink_failure(
    path: Path,
    exc: Exception,
    exc_factory: t.Callable[[Path, Exception], Exception] | None,
) -> t.NoReturn:
    """Handle final unlink failure by raising the appropriate exception."""
    if exc_factory is not None:
        raise exc_factory(path, exc) from exc
    raise exc


def retry_unlink(
    path: Path,
    *,
    config: RetryConfig = DEFAULT_UNLINK_RETRY,
    logger: logging.Logger | None = None,
    exc_factory: t.Callable[[Path, Exception], Exception] | None = None,
) -> None:
    """Unlink *path* with retries for transient filesystem errors."""
    if not path.exists():
        return

    log = logger or _logger
    for attempt in range(config.max_attempts):
        try:
            path.unlink()
            return  # noqa: TRY300
        except FileNotFoundError:
            return
        except (PermissionError, OSError) as exc:
            if attempt == config.max_attempts - 1:
                _handle_unlink_failure(path, exc, exc_factory)

            log.debug(
                "Attempt %d to remove %s failed. Retrying in %.1fs...",
                attempt + 1,
                path,
                config.retry_delay,
            )
            time.sleep(config.retry_delay)


def robust_rmtree(
    path: Path,
    *,
    config: RetryConfig = DEFAULT_RMTREE_RETRY,
    logger: logging.Logger | None = None,
) -> None:
    """Remove a directory tree with retries and clearer errors."""
    if not path.exists():
        return

    log = logger or _logger
    for attempt in range(config.max_attempts):
        try:
            _fix_windows_permissions(path)
            shutil.rmtree(path)
        except OSError as exc:
            if _path_is_missing(path, exc):
                return
            if attempt == config.max_attempts - 1:
                _handle_rmtree_final_failure(path, config.max_attempts, exc, log)

            log.debug(
                "Attempt %d to remove %s failed. Retrying in %.1fs...",
                attempt + 1,
                path,
                config.retry_delay,
            )
            time.sleep(config.retry_delay)
        else:
            _log_rmtree_success(path, log)
            return
