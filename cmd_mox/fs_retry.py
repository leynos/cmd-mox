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
    """
    Configuration for retry loops.

    Attributes
    ----------
    max_attempts : int
        Maximum number of attempts (must be >= 1).
    retry_delay : float
        Delay in seconds between retry attempts (must be >= 0).

    Raises
    ------
    ValueError
        If max_attempts < 1 or retry_delay < 0.
    """

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
    """
    Raised when :func:`robust_rmtree` exhausts all removal attempts.

    Parameters
    ----------
    path : Path
        The directory path that could not be removed.
    attempts : int
        The number of attempts made.
    last_exception : Exception | None
        The last exception encountered during removal.

    Attributes
    ----------
    path : Path
        The directory path that could not be removed.
    attempts : int
        The number of attempts made.
    last_exception : Exception | None
        The last exception encountered during removal.
    """

    def __init__(
        self, path: Path, attempts: int, last_exception: Exception | None
    ) -> None:
        msg = f"Failed to remove {path} after {attempts} attempts"
        super().__init__(msg)
        self.path = path
        self.attempts = attempts
        self.last_exception = last_exception


def _log_retry_attempt(
    logger: logging.Logger, attempt: int, path: Path, retry_delay: float
) -> None:
    """Log a retry attempt with delay information."""
    logger.debug(
        "Attempt %d to remove %s failed. Retrying in %.1fs...",
        attempt + 1,
        path,
        retry_delay,
    )


def _chmod_items(root: Path, items: t.Sequence[os.PathLike[str] | str]) -> None:
    """Apply chmod 0o777 to non-symlink items in a directory."""
    for name in items:
        candidate = root / name
        if candidate.exists() and not candidate.is_symlink():
            candidate.chmod(0o777)


def _fix_windows_permissions(path: Path) -> None:
    """Ensure all files under *path* are writable on Windows before deletion."""
    if not path_utils.IS_WINDOWS:
        return

    for root, dirs, files in os.walk(path):
        root_path = Path(root)
        _chmod_items(root_path, files)
        _chmod_items(root_path, dirs)


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


def retry_unlink(
    path: Path,
    *,
    config: RetryConfig = DEFAULT_UNLINK_RETRY,
    logger: logging.Logger | None = None,
    exc_factory: t.Callable[[Path, Exception], Exception] | None = None,
) -> None:
    """
    Unlink *path* with retries for transient filesystem errors.

    If the path does not exist or is removed during retries (FileNotFoundError),
    the operation succeeds silently. Transient errors (PermissionError, OSError)
    trigger retries with the configured delay.

    Parameters
    ----------
    path : Path
        The file path to remove.
    config : RetryConfig, optional
        Retry configuration (max attempts and delay). Defaults to DEFAULT_UNLINK_RETRY.
    logger : logging.Logger | None, optional
        Logger for retry attempts. Defaults to module logger.
    exc_factory : Callable[[Path, Exception], Exception] | None, optional
        Factory to create custom exception on final failure. If None, re-raises
        the original exception.

    Raises
    ------
    PermissionError, OSError
        When all retry attempts are exhausted (if exc_factory is None).
    Exception
        Custom exception from exc_factory (if provided).
    """
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
            is_last = attempt == config.max_attempts - 1
            if is_last:
                if exc_factory is not None:
                    raise exc_factory(path, exc) from exc
                raise

            _log_retry_attempt(log, attempt, path, config.retry_delay)
            time.sleep(config.retry_delay)


def robust_rmtree(
    path: Path,
    *,
    config: RetryConfig = DEFAULT_RMTREE_RETRY,
    logger: logging.Logger | None = None,
) -> None:
    """
    Remove a directory tree with retries and clearer errors.

    On Windows, ensures all files are writable before removal. If the path does not
    exist or is removed during retries, the operation succeeds silently. Transient
    errors trigger retries with the configured delay.

    Parameters
    ----------
    path : Path
        The directory path to remove.
    config : RetryConfig, optional
        Retry configuration (max attempts and delay). Defaults to DEFAULT_RMTREE_RETRY.
    logger : logging.Logger | None, optional
        Logger for retry attempts and outcomes. Defaults to module logger.

    Raises
    ------
    RobustRmtreeError
        When all retry attempts are exhausted, wrapping the last OSError encountered.
    """
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
            is_last = attempt == config.max_attempts - 1
            if is_last:
                _handle_rmtree_final_failure(path, config.max_attempts, exc, log)

            _log_retry_attempt(log, attempt, path, config.retry_delay)
            time.sleep(config.retry_delay)
        else:
            _log_rmtree_success(path, log)
            return
