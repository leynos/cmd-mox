"""Unit tests for filesystem retry helpers."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

import cmd_mox.fs_retry as fs_retry


def test_retry_unlink_success(tmp_path: Path) -> None:
    """retry_unlink removes an existing file."""
    target = tmp_path / "remove_me.txt"
    target.write_text("data")

    fs_retry.retry_unlink(target)

    assert not target.exists()


def test_retry_unlink_missing_path_noop(tmp_path: Path) -> None:
    """retry_unlink is a no-op for missing paths."""
    missing = tmp_path / "missing.txt"

    fs_retry.retry_unlink(missing)

    assert not missing.exists()


def test_retry_unlink_retries_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """retry_unlink retries transient failures before succeeding."""
    target = tmp_path / "flaky.txt"
    target.write_text("locked")

    attempts: dict[str, int] = {"count": 0}
    original_unlink = Path.unlink

    def flaky_unlink(self: Path) -> None:
        if self == target and attempts["count"] < 2:
            attempts["count"] += 1
            raise PermissionError("locked")
        attempts["count"] += 1
        original_unlink(self)

    monkeypatch.setattr(Path, "unlink", flaky_unlink)
    sleeps: list[float] = []
    monkeypatch.setattr(fs_retry.time, "sleep", sleeps.append)

    fs_retry.retry_unlink(
        target, config=fs_retry.RetryConfig(max_attempts=4, retry_delay=0.25)
    )

    assert attempts["count"] == 3
    assert sleeps == [0.25, 0.25]
    assert not target.exists()


def test_retry_unlink_raises_after_attempts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """retry_unlink surfaces a descriptive error after exhausting retries."""
    target = tmp_path / "stuck.txt"
    target.write_text("locked")

    def locked_unlink(self: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    monkeypatch.setattr(fs_retry.time, "sleep", lambda _duration: None)

    def build_error(path: Path, exc: Exception) -> FileExistsError:
        return FileExistsError(f"Could not remove {path}: {exc}")

    with pytest.raises(FileExistsError, match="Could not remove"):
        fs_retry.retry_unlink(
            target,
            config=fs_retry.RetryConfig(max_attempts=2, retry_delay=0.01),
            exc_factory=build_error,
        )


def test_retry_unlink_propagates_original_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without exc_factory, the original exception should surface."""
    target = tmp_path / "stuck.txt"
    target.write_text("locked")

    def locked_unlink(self: Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    monkeypatch.setattr(fs_retry.time, "sleep", lambda _duration: None)

    with pytest.raises(PermissionError):
        fs_retry.retry_unlink(
            target,
            config=fs_retry.RetryConfig(max_attempts=2, retry_delay=0),
        )


def test_retry_unlink_treats_missing_path_as_success(tmp_path: Path) -> None:
    """TOCTOU-safe: missing path during unlink is treated as success."""
    target = tmp_path / "missing.txt"

    # No file present; should exit quietly.
    fs_retry.retry_unlink(target)


def test_retry_unlink_missing_between_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File disappearing between exists() and unlink() should not error."""
    target = tmp_path / "vanishing.txt"
    target.write_text("tmp")

    original_unlink = Path.unlink

    def remove_then_raise(self: Path) -> None:
        original_unlink(self)
        raise FileNotFoundError("gone")

    monkeypatch.setattr(Path, "unlink", remove_then_raise)

    fs_retry.retry_unlink(
        target, config=fs_retry.RetryConfig(max_attempts=2, retry_delay=0)
    )


@pytest.mark.parametrize(
    ("max_attempts", "retry_delay", "error_msg"),
    [
        (0, 0.1, "max_attempts must be >= 1"),
        (-1, 0.1, "max_attempts must be >= 1"),
        (1, -0.1, "retry_delay must be >= 0"),
    ],
)
def test_retry_config_validation(
    max_attempts: int, retry_delay: float, error_msg: str
) -> None:
    """RetryConfig should validate inputs to avoid silent misconfigurations."""
    with pytest.raises(ValueError, match=error_msg):
        fs_retry.RetryConfig(max_attempts=max_attempts, retry_delay=retry_delay)


def test_robust_rmtree_treats_missing_path_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FileNotFoundError during rmtree is treated as success."""
    target = tmp_path / "gone"
    target.mkdir()

    real_rmtree = shutil.rmtree

    def remove_then_complain(path: Path) -> None:
        real_rmtree(path)
        raise FileNotFoundError

    monkeypatch.setattr(fs_retry.shutil, "rmtree", remove_then_complain)

    fs_retry.robust_rmtree(
        target, config=fs_retry.RetryConfig(max_attempts=2, retry_delay=0)
    )
    assert not target.exists()
