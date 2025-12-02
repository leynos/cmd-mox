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


def _log_retry_attempt(
    logger: logging.Logger | None, attempt: int, path: Path, delay: float
) -> None:
    """Log that a removal attempt failed and will retry."""
    log = logger or _logger
    log.debug(
        "Attempt %d to remove %s failed. Retrying in %.1fs...",
        attempt + 1,
        path,
        delay,
    )


def _fix_windows_permissions(path: Path) -> None:
    """Ensure all files under *path* are writable on Windows before deletion."""
    if not path_utils.IS_WINDOWS:
        return

    for root, dirs, files in os.walk(path):
        for name in files:
            candidate = Path(root) / name
            if candidate.exists() and not candidate.is_symlink():
                candidate.chmod(0o777)
        for name in dirs:
            candidate = Path(root) / name
            if candidate.exists() and not candidate.is_symlink():
                candidate.chmod(0o777)


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

    for attempt in range(config.max_attempts):
        try:
            path.unlink()
            return  # noqa: TRY300
        except (PermissionError, OSError) as exc:
            if attempt == config.max_attempts - 1:
                _handle_unlink_failure(path, exc, exc_factory)
            _log_retry_attempt(logger, attempt, path, config.retry_delay)
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

    last_exception: Exception | None = None
    for attempt in range(config.max_attempts):
        try:
            _fix_windows_permissions(path)
            shutil.rmtree(path)
        except OSError as exc:
            last_exception = exc
            if attempt == config.max_attempts - 1:
                (logger or _logger).warning(
                    "Failed to remove temporary directory %s after %d attempts",
                    path,
                    config.max_attempts,
                )
                raise RobustRmtreeError(
                    path, config.max_attempts, last_exception
                ) from exc
            _log_retry_attempt(logger, attempt, path, config.retry_delay)
            time.sleep(config.retry_delay)
        else:
            (logger or _logger).debug(
                "Successfully removed temporary directory: %s", path
            )
            return

    raise RobustRmtreeError(path, config.max_attempts, last_exception)
