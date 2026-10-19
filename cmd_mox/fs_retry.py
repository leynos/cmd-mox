"""Reusable filesystem cleanup helpers with retry/backoff policies."""

from __future__ import annotations

import dataclasses as dc
import logging
import os
import shutil
import time
import typing as typ
from pathlib import Path

from . import _path_utils as path_utils

if typ.TYPE_CHECKING:
    import collections.abc as cabc

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
        """Validate retry configuration values.

        Raises
        ------
        ValueError
            If the attempt count is less than one or the delay is negative.
        """
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


def _chmod_items(root: Path, items: cabc.Sequence[os.PathLike[str] | str]) -> None:
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
    """Check if the path is missing (from the exception or filesystem).

    Returns
    -------
    bool
        ``True`` when *exc* is a ``FileNotFoundError`` or *path* no longer
        exists.
    """
    return isinstance(exc, FileNotFoundError) or not path.exists()


def _is_file_not_found(exc: OSError) -> bool:
    """Report that *exc* means the file was already removed."""  # ruff: ignore[docstring-missing-returns] - private predicate has an obvious boolean return
    return isinstance(exc, FileNotFoundError)


@dc.dataclass(frozen=True, slots=True)
class _RemovalAttempt:
    """Everything one removal retry loop needs beyond the operation itself.

    Attributes
    ----------
    path : Path
        Path being removed, used for retry and failure logging.
    config : RetryConfig
        Retry policy: maximum attempts and delay between them.
    log : logging.Logger
        Logger for retry attempts.
    handle_final_failure : collections.abc.Callable[[OSError], typing.NoReturn]
        Invoked with the last ``OSError``; it must raise, and the retry loop
        is predicated on that, so it never returns after calling this.
    is_missing : collections.abc.Callable[[OSError], bool]
        Reports that a failed attempt means the path is already gone.
    """

    path: Path
    config: RetryConfig
    log: logging.Logger
    handle_final_failure: cabc.Callable[[OSError], typ.NoReturn]
    is_missing: cabc.Callable[[OSError], bool]


def _removal_retry(
    operation: cabc.Callable[[], None], *, attempt: _RemovalAttempt
) -> bool:
    """Retry *operation* until it succeeds or the attempts run out.

    A path that has already vanished is not a failure, so *is_missing*
    short-circuits the loop. The backoff shape lives here rather than in each
    caller because unlink and rmtree differ only in the operation, how a
    vanished target is recognised, and how the final failure is reported.

    Parameters
    ----------
    operation : collections.abc.Callable[[], None]
        Filesystem operation to attempt.
    attempt : _RemovalAttempt
        Path, retry policy, logger, and the two failure predicates.

    Returns
    -------
    bool
        ``True`` when *operation* completed, ``False`` when the path was
        already absent, so the caller can skip success reporting.
    """
    path = attempt.path
    if not path.exists():
        return False

    for try_index in range(attempt.config.max_attempts):
        try:
            operation()
        except OSError as exc:
            if attempt.is_missing(exc):
                return False
            if try_index == attempt.config.max_attempts - 1:
                attempt.handle_final_failure(exc)

            _log_retry_attempt(attempt.log, try_index, path, attempt.config.retry_delay)
            time.sleep(attempt.config.retry_delay)
        else:
            return True

    # Unreachable: ``RetryConfig`` guarantees at least one attempt and
    # ``handle_final_failure`` raises on the last one.
    return False


def _handle_rmtree_final_failure(
    path: Path, attempts: int, exc: OSError, logger: logging.Logger
) -> typ.NoReturn:
    """Handle final rmtree failure by logging and raising RobustRmtreeError.

    Raises
    ------
    RobustRmtreeError
        Always, wrapping *exc* with the attempt count.
    """
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
    exc_factory: cabc.Callable[[Path, Exception], Exception] | None,
) -> typ.NoReturn:
    """Handle final unlink failure by raising the appropriate exception."""
    if exc_factory is not None:
        raise exc_factory(path, exc) from exc
    raise


def retry_unlink(
    path: Path,
    *,
    config: RetryConfig = DEFAULT_UNLINK_RETRY,
    logger: logging.Logger | None = None,
    exc_factory: cabc.Callable[[Path, Exception], Exception] | None = None,
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
        When all retry attempts are exhausted (if ``exc_factory`` is ``None``).
    Exception
        Custom exception from ``exc_factory`` (if provided).

    """  # ruff: ignore[docstring-extraneous-exception] - _handle_unlink_failure propagates the documented terminal failures.
    _removal_retry(
        path.unlink,
        attempt=_RemovalAttempt(
            path=path,
            config=config,
            log=logger or _logger,
            handle_final_failure=lambda exc: _handle_unlink_failure(
                path, exc, exc_factory
            ),
            is_missing=_is_file_not_found,
        ),
    )


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
        When all retry attempts are exhausted, wrapping the last ``OSError``
        encountered.

    """  # ruff: ignore[docstring-extraneous-exception] - _handle_rmtree_final_failure propagates the documented terminal failure.
    log = logger or _logger

    def remove_tree() -> None:
        # Windows refuses to remove read-only files, so permissions are
        # repaired on every attempt: a partially removed tree can leave new
        # read-only entries behind for the next one to clear.
        _fix_windows_permissions(path)
        shutil.rmtree(path)

    removed = _removal_retry(
        remove_tree,
        attempt=_RemovalAttempt(
            path=path,
            config=config,
            log=log,
            handle_final_failure=lambda exc: _handle_rmtree_final_failure(
                path, config.max_attempts, exc, log
            ),
            is_missing=lambda exc: _path_is_missing(path, exc),
        ),
    )
    if removed:
        _log_rmtree_success(path, log)
