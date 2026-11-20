"""Unit tests for filesystem retry helpers."""

from __future__ import annotations

import os
import shutil
import stat
import typing as typ
from pathlib import Path

import pytest

from cmd_mox import fs_retry

if typ.TYPE_CHECKING:
    import collections.abc as cabc


def _symlink_to(link: Path, target: Path, *, target_is_directory: bool) -> Path:
    """Create *link* pointing at *target*, skipping unsupported platforms."""
    if not hasattr(os, "symlink"):
        pytest.skip("Platform does not support symlinks")
    try:
        link.symlink_to(target, target_is_directory=target_is_directory)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks unavailable: {exc}")
    return link


@pytest.fixture(params=[False, True], ids=["file-target", "directory-target"])
def dangling_symlink(request: pytest.FixtureRequest, tmp_path: Path) -> Path:
    """Provide a symlink whose target does not exist.

    Returns
    -------
    pathlib.Path
        The dangling link.
    """
    return _symlink_to(
        tmp_path / "dangling-link",
        tmp_path / "missing-target",
        target_is_directory=request.param,
    )


def test_retry_unlink_success(tmp_path: Path) -> None:
    """retry_unlink removes an existing file."""
    target = tmp_path / "remove_me.txt"
    target.write_text("data")

    fs_retry.retry_unlink(target)

    assert not target.exists()


def test_retry_unlink_missing_path_noop(tmp_path: Path) -> None:
    """retry_unlink is a no-op for missing paths, and leaves them missing."""
    missing = tmp_path / "missing.txt"

    fs_retry.retry_unlink(missing)

    assert not missing.exists(), "missing path should remain absent"


def test_retry_unlink_retries_then_succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """retry_unlink retries transient failures before succeeding."""
    target = tmp_path / "flaky.txt"
    target.write_text("locked")

    attempts: dict[str, int] = {"count": 0}
    original_unlink = Path.unlink

    def flaky_unlink(self: Path) -> None:
        """Fail the first two unlinks with PermissionError, then remove the file."""
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
        """Fail every unlink with PermissionError."""
        raise PermissionError("locked")

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    monkeypatch.setattr(fs_retry.time, "sleep", lambda _duration: None)

    def build_error(path: Path, exc: Exception) -> FileExistsError:
        """Wrap the last failure in a FileExistsError that names the path."""
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
    """Without exc_factory, the final attempt's exception should surface."""
    target = tmp_path / "stuck.txt"
    target.write_text("locked")
    produced: list[PermissionError] = []

    def locked_unlink(self: Path) -> None:
        """Fail every unlink with a fresh PermissionError."""
        error = PermissionError("locked")
        produced.append(error)
        raise error

    monkeypatch.setattr(Path, "unlink", locked_unlink)
    monkeypatch.setattr(fs_retry.time, "sleep", lambda _duration: None)

    with pytest.raises(PermissionError) as raised:
        fs_retry.retry_unlink(
            target,
            config=fs_retry.RetryConfig(max_attempts=2, retry_delay=0),
        )

    assert len(produced) == 2, "both configured attempts should run"
    assert raised.value is produced[-1], "the final attempt's exception should surface"


def test_retry_unlink_removes_dangling_symlink(dangling_symlink: Path) -> None:
    """A dangling symlink is removed as a link, not treated as already gone."""
    fs_retry.retry_unlink(
        dangling_symlink,
        config=fs_retry.RetryConfig(max_attempts=1, retry_delay=0),
    )

    assert not os.path.lexists(dangling_symlink), "dangling link should be removed"


def test_handle_unlink_failure_raises_supplied_exception() -> None:
    """The final-failure helper raises the supplied exception outside except."""
    error = PermissionError("still locked")

    with pytest.raises(PermissionError) as raised:
        fs_retry._handle_unlink_failure(Path("ignored"), error, None)

    assert raised.value is error, "the supplied exception object should be raised"


def test_retry_unlink_missing_between_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """File disappearing between exists() and unlink() should not error."""
    target = tmp_path / "vanishing.txt"
    target.write_text("tmp")

    original_unlink = Path.unlink
    attempts: dict[str, int] = {"count": 0}

    def remove_then_raise(self: Path) -> None:
        """Remove the file, then raise FileNotFoundError to mimic a race."""
        attempts["count"] += 1
        original_unlink(self)
        raise FileNotFoundError("gone")

    monkeypatch.setattr(Path, "unlink", remove_then_raise)

    fs_retry.retry_unlink(
        target, config=fs_retry.RetryConfig(max_attempts=2, retry_delay=0)
    )

    assert not target.exists(), "vanished file should not be recreated"
    assert attempts["count"] == 1, "FileNotFoundError should end the retry loop"


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

    def remove_then_complain(
        path: Path, *, onexc: cabc.Callable[..., None] | None = None
    ) -> None:
        """Remove the tree, then raise FileNotFoundError as if it had vanished."""
        real_rmtree(path)
        raise FileNotFoundError

    monkeypatch.setattr(fs_retry.shutil, "rmtree", remove_then_complain)

    fs_retry.robust_rmtree(
        target, config=fs_retry.RetryConfig(max_attempts=2, retry_delay=0)
    )
    assert not target.exists()


def test_robust_rmtree_treats_vanished_path_as_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A path gone by the time of the error is success, whatever the error type."""
    target = tmp_path / "raced-away"
    target.mkdir()
    real_rmtree = shutil.rmtree

    def remove_then_deny(
        path: Path, *, onexc: cabc.Callable[..., None] | None = None
    ) -> None:
        """Remove the tree, then raise a non-FileNotFoundError failure."""
        msg = "denied after removal"
        real_rmtree(path)
        raise PermissionError(msg)

    monkeypatch.setattr(fs_retry.shutil, "rmtree", remove_then_deny)

    fs_retry.robust_rmtree(
        target, config=fs_retry.RetryConfig(max_attempts=2, retry_delay=0)
    )

    assert not os.path.lexists(target), "vanished path should not be reported"


def test_robust_rmtree_removes_dangling_symlink(dangling_symlink: Path) -> None:
    """A dangling root symlink is unlinked instead of being treated as absent."""
    fs_retry.robust_rmtree(
        dangling_symlink,
        config=fs_retry.RetryConfig(max_attempts=1, retry_delay=0),
    )

    assert not os.path.lexists(dangling_symlink), "dangling link should be removed"


def test_robust_rmtree_refuses_intact_directory_symlink(tmp_path: Path) -> None:
    """An intact root symlink is refused, leaving the link and target intact."""
    target = tmp_path / "target"
    target.mkdir()
    sentinel = target / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    link = _symlink_to(tmp_path / "intact-link", target, target_is_directory=True)

    with pytest.raises(fs_retry.RobustRmtreeError):
        fs_retry.robust_rmtree(
            link, config=fs_retry.RetryConfig(max_attempts=1, retry_delay=0)
        )

    assert link.is_symlink(), "the root symlink should remain in place"
    assert sentinel.read_text(encoding="utf-8") == "keep", "target must be untouched"


def test_windows_permission_error_clears_read_only_then_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Windows PermissionError chmods the failing entry, then retries it."""
    target = tmp_path / "read-only-tree"
    target.mkdir()
    failing = target / "locked.txt"
    failing.write_text("locked", encoding="utf-8")
    failing_path = os.fspath(failing)
    monkeypatch.setattr(fs_retry.path_utils, "IS_WINDOWS", True)

    events: list[tuple[object, ...]] = []
    unlink_attempts = 0

    def fake_unlink(path: str) -> None:
        """Fail the first removal, then succeed as if chmod helped."""
        nonlocal unlink_attempts
        unlink_attempts += 1
        events.append(("unlink", path))
        if unlink_attempts == 1:
            raise PermissionError(path)

    def record_chmod(path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        """Record the chmod without touching the real filesystem."""
        events.append(("chmod", os.fspath(path), mode))

    def fake_rmtree(
        path: Path, *, onexc: cabc.Callable[..., None] | None = None
    ) -> None:
        """Report one failing unlink, as shutil.rmtree would."""
        assert onexc is not None, "robust_rmtree must pass an onexc handler"
        try:
            fake_unlink(failing_path)
        except PermissionError as exc:
            onexc(fake_unlink, failing_path, exc)
        else:  # pragma: no cover - guards the fake itself
            pytest.fail("fake unlink was expected to fail first")

    monkeypatch.setattr(fs_retry.os, "unlink", fake_unlink)
    monkeypatch.setattr(Path, "chmod", record_chmod)
    monkeypatch.setattr(fs_retry.shutil, "rmtree", fake_rmtree)

    fs_retry.robust_rmtree(
        target, config=fs_retry.RetryConfig(max_attempts=1, retry_delay=0)
    )

    assert events == [
        ("unlink", failing_path),
        ("chmod", failing_path, stat.S_IWRITE),
        ("unlink", failing_path),
    ], "the retried removal should follow the chmod"


@pytest.mark.parametrize(
    ("is_windows", "func", "error", "match"),
    [
        (True, os.unlink, OSError("not a permission failure"), "not a permission"),
        (
            True,
            os.scandir,
            PermissionError("cannot list directory"),
            "cannot list directory",
        ),
        (False, os.unlink, PermissionError("locked"), "locked"),
    ],
    ids=["unrelated-error", "scandir-error", "posix"],
)
def test_permission_error_handler_reraises_without_chmod(
    *,
    is_windows: bool,
    func: cabc.Callable[..., object],
    error: OSError,
    match: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reports the handler cannot repair are re-raised without chmod."""
    monkeypatch.setattr(fs_retry.path_utils, "IS_WINDOWS", is_windows)
    chmod_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(Path, "chmod", lambda *args: chmod_calls.append(args))

    with pytest.raises(type(error), match=match) as raised:
        fs_retry._retry_windows_permission_error(func, "some-path", error)

    assert raised.value is error, "the original exception should be re-raised"
    assert chmod_calls == [], "an unrepairable failure must not chmod"


def test_permission_error_handler_skips_symlink(
    dangling_symlink: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A symlink path is never chmodded, since chmod would follow the link."""
    monkeypatch.setattr(fs_retry.path_utils, "IS_WINDOWS", True)
    chmod_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(Path, "chmod", lambda *args: chmod_calls.append(args))
    error = PermissionError("locked link")

    with pytest.raises(PermissionError, match="locked link") as raised:
        fs_retry._retry_windows_permission_error(
            os.unlink, os.fspath(dangling_symlink), error
        )

    assert raised.value is error, "the symlink report should be re-raised unchanged"
    assert chmod_calls == [], "a symlink must not be chmodded"


@pytest.mark.parametrize("func", [os.unlink, os.scandir], ids=["unlink", "scandir"])
def test_permission_error_handler_skips_vanished_entry(
    func: cabc.Callable[..., object], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A FileNotFoundError report is skipped so rmtree can continue."""
    monkeypatch.setattr(fs_retry.path_utils, "IS_WINDOWS", True)
    chmod_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(Path, "chmod", lambda *args: chmod_calls.append(args))

    fs_retry._retry_windows_permission_error(
        func, "vanished", FileNotFoundError("vanished")
    )

    assert chmod_calls == [], "a vanished entry must not be chmodded"


def test_permission_error_handler_skips_entry_vanished_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FileNotFoundError from the retried removal is skipped too."""
    monkeypatch.setattr(fs_retry.path_utils, "IS_WINDOWS", True)
    chmod_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(Path, "chmod", lambda *args: chmod_calls.append(args))
    vanished = FileNotFoundError("vanished")

    def vanished_unlink(path: str) -> None:
        """Fail as if the entry disappeared after the chmod."""
        raise vanished

    monkeypatch.setattr(fs_retry.os, "unlink", vanished_unlink)

    fs_retry._retry_windows_permission_error(
        vanished_unlink, "vanished", PermissionError("locked")
    )

    assert len(chmod_calls) == 1, "the repair should be attempted once"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions only")
def test_retry_unlink_reports_unsearchable_parent(tmp_path: Path) -> None:
    """An unsearchable parent is an access failure, not an absent target."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    parent = tmp_path / "sealed"
    parent.mkdir()
    target = parent / "hidden.txt"
    target.write_text("data", encoding="utf-8")
    parent.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            fs_retry.retry_unlink(
                target,
                config=fs_retry.RetryConfig(max_attempts=1, retry_delay=0),
            )
    finally:
        parent.chmod(0o700)

    assert target.exists(), "the target was not removed, so it must survive"


def test_robust_rmtree_retries_when_presence_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An access error during cleanup triggers retries, not a false success."""
    target = tmp_path / "partially-removed"
    target.mkdir()
    attempts: list[int] = []

    def deny_lstat(self: Path) -> os.stat_result:
        """Simulate a parent that denies access after a partial removal."""
        raise PermissionError(os.fspath(self))

    def failing_rmtree(
        path: Path, *, onexc: cabc.Callable[..., None] | None = None
    ) -> None:
        """Report a permission failure without removing the root."""
        attempts.append(1)
        raise PermissionError(os.fspath(path))

    # Isolate the presence check: Python 3.12's is_symlink() calls lstat()
    # itself and would otherwise fail before the removal operation runs.
    monkeypatch.setattr(Path, "is_symlink", lambda _self: False)
    monkeypatch.setattr(Path, "lstat", deny_lstat)
    monkeypatch.setattr(fs_retry.shutil, "rmtree", failing_rmtree)

    with pytest.raises(fs_retry.RobustRmtreeError):
        fs_retry.robust_rmtree(
            target, config=fs_retry.RetryConfig(max_attempts=2, retry_delay=0)
        )

    assert len(attempts) == 2, "the failure must be retried, not treated as absence"


@pytest.mark.skipif(os.name == "nt", reason="POSIX directory permissions only")
def test_robust_rmtree_refuses_link_to_unsearchable_target(tmp_path: Path) -> None:
    """An inaccessible link target is a failure, not a dangling link."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    sealed = tmp_path / "sealed"
    sealed.mkdir()
    target = sealed / "target"
    target.mkdir()
    link = _symlink_to(tmp_path / "link", target, target_is_directory=True)
    sealed.chmod(0o000)
    try:
        with pytest.raises(fs_retry.RobustRmtreeError):
            fs_retry.robust_rmtree(
                link, config=fs_retry.RetryConfig(max_attempts=1, retry_delay=0)
            )
    finally:
        sealed.chmod(0o700)

    assert link.is_symlink(), "the intact link must survive the failed removal"
    assert target.is_dir(), "the link target must be untouched"


def test_rmtree_wraps_chmod_failure_as_last_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chmod failure propagates and becomes the wrapped last exception."""
    target = tmp_path / "tree"
    target.mkdir()
    failing = target / "locked.txt"
    failing.write_text("locked", encoding="utf-8")
    chmod_error = OSError("chmod denied")
    monkeypatch.setattr(fs_retry.path_utils, "IS_WINDOWS", True)

    def failing_chmod(path: Path, mode: int, *, follow_symlinks: bool = True) -> None:
        """Fail the read-only repair."""
        raise chmod_error

    def fake_rmtree(
        path: Path, *, onexc: cabc.Callable[..., None] | None = None
    ) -> None:
        """Report one failing unlink, as shutil.rmtree would."""
        assert onexc is not None, "robust_rmtree must pass an onexc handler"
        onexc(os.unlink, os.fspath(failing), PermissionError("locked"))

    monkeypatch.setattr(Path, "chmod", failing_chmod)
    monkeypatch.setattr(fs_retry.shutil, "rmtree", fake_rmtree)

    with pytest.raises(fs_retry.RobustRmtreeError) as raised:
        fs_retry.robust_rmtree(
            target, config=fs_retry.RetryConfig(max_attempts=1, retry_delay=0)
        )

    assert raised.value.__cause__ is chmod_error, "the chmod failure is the cause"
    assert raised.value.last_exception is chmod_error, "the chmod failure is reported"
