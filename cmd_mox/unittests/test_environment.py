"""Unit tests for :mod:`cmd_mox.environment`."""

from __future__ import annotations

import logging
import os
import stat
import typing as t
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import cmd_mox.environment as env_mod
from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    EnvironmentManager,
    _attempt_single_removal,
    _fix_windows_permissions,
    _handle_final_failure,
    _log_retry_attempt,
    _retry_removal,
    _robust_rmtree,
    temporary_env,
)


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

        _robust_rmtree(test_dir, max_retries=2, retry_delay=0.01)

        assert mock_rmtree.call_count == 2


def test_robust_rmtree_max_retries_exceeded(tmp_path: Path) -> None:
    """Test that _robust_rmtree raises after max retries exceeded."""
    test_dir = tmp_path / "test_fail"
    test_dir.mkdir()

    with patch("cmd_mox.environment.shutil.rmtree") as mock_rmtree:
        mock_rmtree.side_effect = OSError("Persistent permission denied")

        with pytest.raises(OSError, match="Persistent permission denied"):
            _robust_rmtree(test_dir, max_retries=1, retry_delay=0.01)

        assert mock_rmtree.call_count == 2  # Initial + 1 retry


def test_retry_removal_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_retry_removal should retry failed attempts until success."""
    calls: list[bool] = []

    def fake_attempt(path: Path, *, raise_on_error: bool) -> bool:
        calls.append(raise_on_error)
        return len(calls) == 2

    monkeypatch.setattr(env_mod, "_attempt_single_removal", fake_attempt)
    monkeypatch.setattr(env_mod, "_log_retry_attempt", lambda *args: None)
    monkeypatch.setattr(env_mod.time, "sleep", lambda _: None)
    _retry_removal(tmp_path, max_retries=3, retry_delay=0.01)
    assert calls == [False, False]


def test_retry_removal_calls_final_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_retry_removal delegates to _handle_final_failure after retries."""

    def fake_attempt(path: Path, *, raise_on_error: bool) -> bool:
        raise OSError("boom")

    called: dict[str, t.Any] = {}
    monkeypatch.setattr(env_mod, "_attempt_single_removal", fake_attempt)
    monkeypatch.setattr(env_mod, "_log_retry_attempt", lambda *args: None)
    monkeypatch.setattr(env_mod.time, "sleep", lambda _: None)

    def fake_handle(path: Path, retries: int, exc: Exception | None) -> None:
        called["args"] = (path, retries, exc)
        raise RuntimeError("stop")

    monkeypatch.setattr(env_mod, "_handle_final_failure", fake_handle)
    with pytest.raises(RuntimeError, match="stop"):
        _retry_removal(tmp_path, max_retries=1, retry_delay=0.01)
    assert isinstance(called.get("args", (None, None, None))[2], OSError)


def test_attempt_single_removal_success(tmp_path: Path) -> None:
    """_attempt_single_removal removes the directory once."""
    test_dir = tmp_path / "single_success"
    test_dir.mkdir()
    with patch("cmd_mox.environment._fix_windows_permissions") as fix_perm:
        assert _attempt_single_removal(test_dir, raise_on_error=True)
        fix_perm.assert_called_once_with(test_dir)
    assert not test_dir.exists()


def test_attempt_single_removal_error_handling(tmp_path: Path) -> None:
    """_attempt_single_removal handles errors based on raise_on_error."""
    test_dir = tmp_path / "single_fail"
    test_dir.mkdir()
    with patch("cmd_mox.environment.shutil.rmtree", side_effect=OSError("boom")):
        assert not _attempt_single_removal(test_dir, raise_on_error=False)
        with pytest.raises(OSError, match="boom"):
            _attempt_single_removal(test_dir, raise_on_error=True)


def test_fix_windows_permissions_noop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_fix_windows_permissions is a no-op on non-Windows systems."""
    called = False

    def fake_walk(_: Path) -> list[tuple[str, list[str], list[str]]]:
        nonlocal called
        called = True
        return []

    fake_os = SimpleNamespace(name="posix", walk=fake_walk)
    monkeypatch.setattr(env_mod, "os", fake_os)
    _fix_windows_permissions(tmp_path)
    assert not called


def test_fix_windows_permissions_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_fix_windows_permissions makes files and dirs writable on Windows."""
    test_dir = tmp_path / "perm"
    test_dir.mkdir()
    file_path = test_dir / "file.txt"
    file_path.write_text("x")
    file_path.chmod(stat.S_IREAD)
    sub_dir = test_dir / "sub"
    sub_dir.mkdir()
    sub_dir.chmod(stat.S_IREAD)
    fake_os = SimpleNamespace(name="nt", walk=os.walk)
    monkeypatch.setattr(env_mod, "os", fake_os)
    _fix_windows_permissions(test_dir)
    assert (file_path.stat().st_mode & 0o777) == 0o777
    assert (sub_dir.stat().st_mode & 0o777) == 0o777


def test_log_retry_attempt_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """_log_retry_attempt logs a helpful debug message."""
    path = Path("foo")
    with caplog.at_level(logging.DEBUG):
        _log_retry_attempt(0, path, 0.5)
    assert f"Attempt 1 to remove {path} failed" in caplog.text


def test_handle_final_failure_reraises(tmp_path: Path) -> None:
    """_handle_final_failure re-raises the original exception when provided."""
    err = OSError("boom")
    with patch("cmd_mox.environment.logger.warning") as warn:
        with pytest.raises(OSError, match="boom") as exc:
            _handle_final_failure(tmp_path, 1, err)
        warn.assert_called_once()
    assert exc.value is err


def test_handle_final_failure_new_error(tmp_path: Path) -> None:
    """_handle_final_failure raises a new error when no exception is stored."""
    with patch("cmd_mox.environment.logger.warning") as warn:
        with pytest.raises(OSError, match="Failed to remove"):
            _handle_final_failure(tmp_path, 2, None)
        warn.assert_called_once()


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


def test_environment_manager_cleanup_error_handling() -> None:
    """Test EnvironmentManager handles cleanup errors appropriately."""
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
