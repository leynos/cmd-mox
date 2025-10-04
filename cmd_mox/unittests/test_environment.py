"""Unit tests for :mod:`cmd_mox.environment`."""

from __future__ import annotations

import os
import stat
import threading
import typing as t
from dataclasses import dataclass  # noqa: ICN003
from pathlib import Path
from unittest.mock import patch

import pytest

import cmd_mox.environment as envmod
from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    CleanupError,
    EnvironmentManager,
    RobustRmtreeError,
    _robust_rmtree,
    temporary_env,
)

if t.TYPE_CHECKING:  # pragma: no cover - imported for type hints
    from pathlib import Path


def test_environment_manager_modifies_and_restores() -> None:
    """Path and env variables should be modified and later restored."""
    original_env = os.environ.copy()
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        assert env.shim_dir.exists()
        assert os.environ["PATH"].split(os.pathsep)[0] == str(env.shim_dir)
        assert os.environ[CMOX_IPC_SOCKET_ENV] == str(env.socket_path)
        os.environ["EXTRA_VAR"] = "temp"
    assert os.environ == original_env
    assert env.shim_dir is not None
    assert not env.shim_dir.exists()


def test_export_ipc_environment_sets_timeout() -> None:
    """export_ipc_environment exposes timeout overrides when provided."""
    with EnvironmentManager() as env:
        env.export_ipc_environment(timeout=2.5)
        assert os.environ[CMOX_IPC_TIMEOUT_ENV] == "2.5"
        assert env.ipc_timeout == 2.5


def test_export_ipc_environment_rejects_inactive_manager() -> None:
    """Calling export_ipc_environment before entering raises."""
    mgr = EnvironmentManager()
    with pytest.raises(RuntimeError, match="Cannot export IPC settings"):
        mgr.export_ipc_environment(timeout=1.0)


def test_environment_restores_modified_vars() -> None:
    """User-modified variables inside context should revert on exit."""
    os.environ["TEST_VAR"] = "before"
    with EnvironmentManager():
        os.environ["TEST_VAR"] = "inside"
    assert os.environ["TEST_VAR"] == "before"
    del os.environ["TEST_VAR"]


def test_environment_manager_restores_on_exception() -> None:
    """Environment is restored even if the context body raises."""
    original_env = os.environ.copy()
    holder: dict[str, Path | None] = {"path": None}

    def trigger_error() -> None:
        with EnvironmentManager() as env:
            holder["path"] = env.shim_dir
            assert env.shim_dir is not None
            assert env.shim_dir.exists()
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        trigger_error()
    assert os.environ == original_env
    assert holder["path"] is not None
    assert not holder["path"].exists()


def test_environment_manager_nested_raises() -> None:
    """Nesting EnvironmentManager should raise RuntimeError."""
    original_env = os.environ.copy()
    outer = EnvironmentManager()
    with outer as env:
        with pytest.raises(RuntimeError):
            EnvironmentManager().__enter__()
        assert os.environ["PATH"].split(os.pathsep)[0] == str(env.shim_dir)
    assert os.environ == original_env


def test_environment_restores_deleted_vars() -> None:
    """Deletion of variables inside context is undone on exit."""
    os.environ["DEL_VAR"] = "before"
    with EnvironmentManager():
        del os.environ["DEL_VAR"]
    assert os.environ["DEL_VAR"] == "before"
    del os.environ["DEL_VAR"]


def test_temporary_env_restores_environment() -> None:
    """temporary_env should fully restore the process environment."""
    original_env = os.environ.copy()
    with temporary_env({"TMP": "1"}):
        os.environ["EXTRA"] = "foo"
        assert os.environ["TMP"] == "1"
        assert os.environ["EXTRA"] == "foo"
    assert os.environ == original_env


def test_temporary_env_restores_deleted_vars() -> None:
    """Variables deleted inside temporary_env are re-added."""
    os.environ["KEEP"] = "val"
    with temporary_env({"TMP": "x"}):
        del os.environ["KEEP"]
    assert os.environ["KEEP"] == "val"
    del os.environ["KEEP"]


def test_temporary_env_restores_on_exception() -> None:
    """temporary_env should restore the env even if an error occurs."""
    original_env = os.environ.copy()

    def trigger() -> None:
        with temporary_env({"ERR": "1"}):
            os.environ["EXTRA"] = "bar"
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        trigger()
    assert os.environ == original_env


def test_nested_temporary_env() -> None:
    """Nested temporary_env contexts restore environment correctly."""
    original_env = os.environ.copy()
    with temporary_env({"A": "1"}):
        with temporary_env({"B": "2"}):
            os.environ["C"] = "3"
        assert "B" not in os.environ
        assert os.environ["A"] == "1"
        assert "C" not in os.environ
    assert os.environ == original_env


def test_robust_rmtree_success(tmp_path: Path) -> None:
    """Test that _robust_rmtree successfully removes a directory."""
    test_dir = tmp_path / "test_remove"
    test_dir.mkdir()
    (test_dir / "file.txt").write_text("test")

    assert test_dir.exists()
    _robust_rmtree(test_dir)
    assert not test_dir.exists()


def test_robust_rmtree_nonexistent_path(tmp_path: Path) -> None:
    """Test that _robust_rmtree handles nonexistent paths gracefully."""
    nonexistent = tmp_path / "does_not_exist"
    assert not nonexistent.exists()
    _robust_rmtree(nonexistent)  # Should not raise


def test_robust_rmtree_retry_on_failure(tmp_path: Path) -> None:
    """Test that _robust_rmtree retries on failure."""
    test_dir = tmp_path / "test_retry"
    test_dir.mkdir()

    with patch("cmd_mox.environment.shutil.rmtree") as mock_rmtree:
        # Simulate transient failure followed by success
        mock_rmtree.side_effect = [OSError("Permission denied"), None]

        _robust_rmtree(test_dir, max_attempts=3, retry_delay=0.01)

        assert mock_rmtree.call_count == 2


def test_robust_rmtree_max_attempts_exceeded(tmp_path: Path) -> None:
    """Test that _robust_rmtree raises after max attempts exceeded."""
    test_dir = tmp_path / "test_fail"
    test_dir.mkdir()

    with patch("cmd_mox.environment.shutil.rmtree") as mock_rmtree:
        mock_rmtree.side_effect = OSError("Persistent permission denied")

        with pytest.raises(RobustRmtreeError) as exc:
            _robust_rmtree(test_dir, max_attempts=2, retry_delay=0.01)

        assert mock_rmtree.call_count == 2  # Initial + 1 retry
    assert isinstance(exc.value.__cause__, OSError)


def test_environment_manager_robust_cleanup_success() -> None:
    """Test EnvironmentManager uses robust cleanup successfully."""
    original_env = os.environ.copy()

    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        test_file = env.shim_dir / "test.txt"
        test_file.write_text("test content")
        assert test_file.exists()

    assert os.environ == original_env
    assert env.shim_dir is not None
    assert not env.shim_dir.exists()


def test_environment_manager_cleanup_error_basic() -> None:
    """Ensure EnvironmentManager handles generic cleanup errors."""
    original_env = os.environ.copy()

    with patch("cmd_mox.environment._robust_rmtree") as mock_rmtree:
        mock_rmtree.side_effect = OSError("Cleanup failed")

        with pytest.raises(RuntimeError, match="Cleanup failed"), EnvironmentManager():
            pass

    # Environment should still be restored despite cleanup failure
    assert os.environ == original_env


def test_environment_manager_cleanup_error_during_exception() -> None:
    """Test cleanup errors are logged but don't mask original exceptions."""
    original_env = os.environ.copy()

    with patch("cmd_mox.environment._robust_rmtree") as mock_rmtree:
        mock_rmtree.side_effect = OSError("Cleanup failed")

        # Original exception should be preserved, cleanup error logged
        msg = "original error"
        with (
            pytest.raises(ValueError, match="original error"),
            EnvironmentManager(),
        ):
            raise ValueError(msg)

    assert os.environ == original_env


@dataclass(frozen=True)
class CleanupErrorTestConfig:
    """Configuration for cleanup error test scenarios."""

    check_cause: bool = False
    manual_restore: bool = False


@dataclass(frozen=True)
class CleanupErrorTestCase:
    """Test parameters for cleanup error scenarios."""

    mock_target: str
    error_message: str
    expected_error_pattern: str
    config: CleanupErrorTestConfig


def _test_environment_cleanup_error(
    test_case: CleanupErrorTestCase,
) -> None:
    """Validate cleanup error handling and state restoration."""
    original_env = os.environ.copy()

    with patch(test_case.mock_target) as mock_method:
        mock_method.side_effect = OSError(test_case.error_message)
        mgr = EnvironmentManager()
        with (
            pytest.raises(
                RuntimeError, match=test_case.expected_error_pattern
            ) as excinfo,
            mgr,
        ):
            pass
        assert envmod.EnvironmentManager.get_active_manager() is None
        if test_case.config.check_cause:
            assert isinstance(excinfo.value.__cause__, OSError)
            assert str(excinfo.value.__cause__) == test_case.error_message

    if test_case.config.manual_restore:
        envmod._restore_env(original_env)

    assert os.environ == original_env


@pytest.mark.parametrize(
    "test_case",
    [
        CleanupErrorTestCase(
            "cmd_mox.environment._restore_env",
            "restore failed",
            "Cleanup failed: Environment restoration failed: restore failed",
            CleanupErrorTestConfig(manual_restore=True),
        ),
        CleanupErrorTestCase(
            "cmd_mox.environment._robust_rmtree",
            "rmtree failed",
            "Cleanup failed: Directory cleanup failed: rmtree failed",
            CleanupErrorTestConfig(check_cause=True),
        ),
    ],
)
def test_environment_manager_cleanup_error_handling(
    test_case: CleanupErrorTestCase,
) -> None:
    """Validate cleanup errors raise RuntimeError and reset state."""
    _test_environment_cleanup_error(test_case)


def test_should_skip_directory_removal_no_directory() -> None:
    """Return ``True`` when no directory was created or it's gone."""
    mgr = EnvironmentManager()
    assert mgr._should_skip_directory_removal()


def test_should_skip_directory_removal_missing_shim(tmp_path: Path) -> None:
    """Return ``True`` when the created directory was removed."""
    mgr = EnvironmentManager()
    path = tmp_path / "missing"
    path.mkdir()
    mgr._created_dir = path
    mgr.shim_dir = path
    path.rmdir()
    assert mgr._should_skip_directory_removal()


def test_should_skip_directory_removal_replaced(tmp_path: Path) -> None:
    """Return ``True`` when ``shim_dir`` differs from ``_created_dir``."""
    mgr = EnvironmentManager()
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    original.mkdir()
    replacement.mkdir()
    mgr._created_dir = original
    mgr.shim_dir = replacement
    assert mgr._should_skip_directory_removal()


def test_should_skip_directory_removal_returns_false(tmp_path: Path) -> None:
    """Return ``False`` when the original directory still exists."""
    mgr = EnvironmentManager()
    path = tmp_path / "dir"
    path.mkdir()
    mgr._created_dir = path
    mgr.shim_dir = path
    assert not mgr._should_skip_directory_removal()


def test_cleanup_temporary_directory_skips_when_no_directory() -> None:
    """Skip directory removal when no directory was created or it's gone."""
    mgr = EnvironmentManager()
    cleanup_errors: list[CleanupError] = []
    with patch("cmd_mox.environment._robust_rmtree") as rm:
        cleanup_dir = t.cast(
            "t.Callable[[list[CleanupError]], None]",
            mgr._cleanup_temporary_directory,
        )
        cleanup_dir(cleanup_errors)
        assert not cleanup_errors
    rm.assert_not_called()
    assert mgr._created_dir is None


def test_cleanup_temporary_directory_skips_when_shim_dir_missing(
    tmp_path: Path,
) -> None:
    """Skip directory removal if ``shim_dir`` was removed externally."""
    mgr = EnvironmentManager()
    path = tmp_path / "missing"
    path.mkdir()
    mgr._created_dir = path
    mgr.shim_dir = path
    path.rmdir()
    cleanup_errors: list[CleanupError] = []
    with patch("cmd_mox.environment._robust_rmtree") as rm:
        cleanup_dir = t.cast(
            "t.Callable[[list[CleanupError]], None]",
            mgr._cleanup_temporary_directory,
        )
        cleanup_dir(cleanup_errors)
        assert not cleanup_errors
    rm.assert_not_called()
    assert mgr._created_dir is None


def test_cleanup_temporary_directory_skips_when_shim_dir_replaced(
    tmp_path: Path,
) -> None:
    """Skip directory removal when ``shim_dir`` differs from ``_created_dir``."""
    mgr = EnvironmentManager()
    original = tmp_path / "original"
    replacement = tmp_path / "replacement"
    original.mkdir()
    replacement.mkdir()
    mgr._created_dir = original
    mgr.shim_dir = replacement
    cleanup_errors: list[CleanupError] = []
    with patch("cmd_mox.environment._robust_rmtree") as rm:
        cleanup_dir = t.cast(
            "t.Callable[[list[CleanupError]], None]",
            mgr._cleanup_temporary_directory,
        )
        cleanup_dir(cleanup_errors)
        assert not cleanup_errors
    rm.assert_not_called()
    assert mgr._created_dir is None
    assert original.exists()
    assert replacement.exists()


def test_environment_manager_readonly_file_cleanup(tmp_path: Path) -> None:
    """Test that cleanup handles read-only files appropriately."""
    # This test primarily checks the Windows-specific path but runs on all platforms
    test_dir = tmp_path / "readonly_test"
    test_dir.mkdir()
    readonly_file = test_dir / "readonly.txt"
    readonly_file.write_text("readonly content")

    # Make file read-only
    readonly_file.chmod(stat.S_IREAD)

    # The robust cleanup should handle this
    _robust_rmtree(test_dir)
    assert not test_dir.exists()


def test_active_manager_is_thread_local() -> None:
    """Each thread should track its own active EnvironmentManager."""
    results: list[EnvironmentManager | None] = []

    def check_manager() -> None:
        results.append(EnvironmentManager.get_active_manager())

    with EnvironmentManager():
        thread = threading.Thread(target=check_manager)
        thread.start()
        thread.join()

    assert results == [None]
