"""Unit tests covering shim generation for POSIX and Windows platforms."""

from __future__ import annotations

import os
import pathlib
import stat
import sys
import tempfile
import typing as typ

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox import shimgen
from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    EnvironmentManager,
)
from cmd_mox.ipc import IPCServer
from cmd_mox.shimgen import (
    _escape_batch_literal,
    _format_windows_launcher,
    _validate_command_uniqueness,
    _validate_no_nul_bytes,
    _validate_no_path_separators,
    _validate_not_dot_directories,
    _validate_not_empty,
)

if typ.TYPE_CHECKING:  # pragma: no cover - typing helpers only
    import collections.abc as cabc
    import subprocess

    from cmd_mox.fs_retry import RetryConfig


_HEALTHY_SYMLINK_REWRITE_MESSAGE = "healthy symlink must not be recreated"
_MATCHING_LAUNCHER_REWRITE_MESSAGE = "matching launcher must not be rewritten"


def _assert_is_shim(path: pathlib.Path) -> None:
    """Assert that *path* points to a usable shim implementation."""
    if path_utils.IS_WINDOWS:
        assert path.suffix == ".cmd"
        content = path.read_text(encoding="utf-8")
        assert sys.executable in content
        assert os.fspath(shimgen.SHIM_PATH) in content
    else:
        assert path.is_symlink()
        assert path.resolve() == shimgen.SHIM_PATH
        assert os.access(path, os.X_OK)


def test_format_windows_launcher_escapes_quotes(tmp_path: pathlib.Path) -> None:
    """Batch launchers should double quotes in Python and shim paths."""
    python_exe = tmp_path / 'python "with" quotes.exe'
    shim_script = tmp_path / 'shim "quoted".py'
    python_text = str(python_exe)
    shim_text = os.fspath(shim_script)
    content = shimgen._format_windows_launcher(python_text, shim_script)
    python_escaped = python_text.replace('"', '""')
    shim_escaped = shim_text.replace('"', '""')
    assert f'"{python_escaped}"' in content
    assert f'"{shim_escaped}"' in content


def test_format_windows_launcher_escapes_carets_and_percents(
    tmp_path: pathlib.Path,
) -> None:
    """Carets and percent signs should be doubled for batch safety."""
    python_exe = tmp_path / "py^%thon.exe"
    shim_script = tmp_path / "shim^%script.py"
    content = shimgen._format_windows_launcher(str(python_exe), shim_script)

    def expected(path: pathlib.Path) -> str:
        """Build the quoted path literal with carets and percents doubled."""
        escaped = str(path).replace("^", "^^").replace("%", "%%").replace('"', '""')
        return f'"{escaped}"'

    assert expected(python_exe) in content
    assert expected(shim_script) in content


def test_format_windows_launcher_includes_delayed_expansion_comment(
    tmp_path: pathlib.Path,
) -> None:
    """Launcher should document why delayed expansion is disabled."""
    content = _format_windows_launcher(
        str(tmp_path / "python.exe"), tmp_path / "shim.py"
    )
    assert "DISABLEDELAYEDEXPANSION" in content
    assert "exclamation marks" in content


def test_escape_batch_literal_escapes_metacharacters() -> None:
    """Batch literals should escape carets, percents, and quotes."""
    literal = '^%"path"'
    escaped = _escape_batch_literal(literal)
    assert escaped == '^^%%""path""'


def test_validate_command_uniqueness_respects_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Uniqueness checks should reflect filesystem case sensitivity."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", False)
    _validate_command_uniqueness(["git", "GIT"])

    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    with pytest.raises(ValueError, match="Duplicate command names"):
        _validate_command_uniqueness(["git", "GIT"])


@pytest.mark.requires_unix_sockets
@pytest.mark.skipif(path_utils.IS_WINDOWS, reason="POSIX symlink execution only")
def test_create_shims_and_execution(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """POSIX shims execute the shared shim and expose the command name."""
    commands = ["git", "curl"]
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        assert env.socket_path is not None
        with IPCServer(env.socket_path):
            mapping = shimgen.create_shims(env.shim_dir, commands)
            assert set(mapping) == set(commands)
            assert os.environ[CMOX_IPC_SOCKET_ENV] == str(env.socket_path)
            assert os.environ[CMOX_IPC_TIMEOUT_ENV] == "5.0"
            for cmd in commands:
                link = mapping[cmd]
                _assert_is_shim(link)
                result = run([str(link)])
                assert result.stdout.strip() == cmd
                assert result.stderr == ""
                assert result.returncode == 0


def test_create_windows_launchers(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows builds emit ``.cmd`` launchers wrapping ``shim.py``."""
    if not path_utils.IS_WINDOWS:
        monkeypatch.setattr(path_utils, "IS_WINDOWS", True)

    mapping = shimgen.create_shims(tmp_path, ["git"])
    assert set(mapping) == {"git"}
    launcher = mapping["git"]
    assert launcher.suffix == ".cmd"
    content = launcher.read_text(encoding="utf-8")
    assert sys.executable in content
    assert os.fspath(shimgen.SHIM_PATH) in content


def test_create_windows_launchers_use_crlf(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows launchers should always use CRLF delimiters."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    mapping = shimgen.create_shims(tmp_path, ["git"])
    launcher = mapping["git"]
    assert b"\r\n" in launcher.read_bytes()


def test_create_windows_shim_retries_locked_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Launcher creation retries when Windows reports transient locks."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    launcher = tmp_path / "git.cmd"
    launcher.write_text("old")

    original_unlink = pathlib.Path.unlink
    attempts: list[int] = [0]

    def flaky_unlink(self: pathlib.Path) -> None:
        """Fail with a lock twice, then unlink the launcher normally."""
        if self == launcher and attempts[0] < 2:
            attempts[0] += 1
            raise PermissionError("locked")
        attempts[0] += 1
        original_unlink(self)

    monkeypatch.setattr(pathlib.Path, "unlink", flaky_unlink)
    sleeps: list[float] = []
    monkeypatch.setattr("cmd_mox.fs_retry.time.sleep", sleeps.append)

    mapping = shimgen.create_shims(tmp_path, ["git"])
    assert mapping["git"].exists()
    assert attempts[0] == 3
    assert sleeps == [0.5, 0.5]


def test_create_windows_shim_raises_after_retries(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exhausting retries surfaces a clear ``FileExistsError``."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    launcher = tmp_path / "git.cmd"
    launcher.write_text("old")

    def locked_unlink(self: pathlib.Path) -> None:
        """Raise a PermissionError on every call to simulate a locked launcher."""
        raise PermissionError("locked")

    monkeypatch.setattr(pathlib.Path, "unlink", locked_unlink)
    monkeypatch.setattr("cmd_mox.fs_retry.time.sleep", lambda _duration: None)

    with pytest.raises(FileExistsError, match="Failed to remove existing launcher"):
        shimgen.create_shims(tmp_path, ["git"])


def test_create_windows_shim_uses_shared_retry_config(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Launcher removal should use the shared unlink retry config."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    launcher = tmp_path / "git.cmd"
    launcher.write_text("old")

    calls: list[tuple[pathlib.Path, RetryConfig]] = []

    def fake_retry_unlink(
        path: pathlib.Path,
        *,
        config: RetryConfig,
        logger: object | None = None,
        exc_factory: object | None = None,
    ) -> None:
        """Record the path and retry config, then unlink the launcher directly."""
        calls.append((path, config))
        launcher.unlink()  # succeed immediately

    monkeypatch.setattr(shimgen, "retry_unlink", fake_retry_unlink)

    shimgen.create_shims(tmp_path, ["git"])

    assert calls == [(launcher, shimgen.DEFAULT_UNLINK_RETRY)]


def test_create_windows_shim_retry_failure_message(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failure after all retries should surface the configured message."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    launcher = tmp_path / "git.cmd"
    launcher.write_text("old")

    def locked_unlink(self: pathlib.Path) -> None:
        """Raise a PermissionError on every call to simulate a locked launcher."""
        raise PermissionError("locked")

    monkeypatch.setattr(pathlib.Path, "unlink", locked_unlink)
    monkeypatch.setattr("cmd_mox.fs_retry.time.sleep", lambda _duration: None)

    msg = "Failed to remove existing launcher"
    with pytest.raises(FileExistsError, match=msg):
        shimgen.create_shims(tmp_path, ["git"])


def test_create_shims_missing_target_dir() -> None:
    """Error raised when directory does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        missing = pathlib.Path(tmpdir) / "absent"
        with pytest.raises(FileNotFoundError):
            shimgen.create_shims(missing, ["ls"])


def test_create_shims_rejects_non_directory(tmp_path: pathlib.Path) -> None:
    """Non-directory shim paths raise ``FileNotFoundError`` with context."""
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("content")
    with pytest.raises(FileNotFoundError, match="not a directory"):
        shimgen.create_shims(file_path, ["ls"])


def test_create_shims_existing_non_symlink_file() -> None:
    """Error raised when an existing file collides with the shim name."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = pathlib.Path(tmpdir)
        file_name = "ls.cmd" if path_utils.IS_WINDOWS else "ls"
        file_path = path / file_name
        file_path.write_text("not a shim")
        with pytest.raises(FileExistsError):
            shimgen.create_shims(path, ["ls"])


def test_create_shims_missing_or_non_executable_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle missing or non-executable shim templates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tempdir = pathlib.Path(tmpdir)
        missing_shim = tempdir / "missing"
        monkeypatch.setattr("cmd_mox.shimgen.SHIM_PATH", missing_shim)
        with pytest.raises(FileNotFoundError):
            shimgen.create_shims(tempdir, ["ls"])

        shim_path = tempdir / "fake_shim"
        shim_path.write_text("#!/bin/sh\necho fake")
        shim_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        monkeypatch.setattr("cmd_mox.shimgen.SHIM_PATH", shim_path)
        mapping = shimgen.create_shims(tempdir, ["ls"])
        _assert_is_shim(mapping["ls"])
        assert os.access(shim_path, os.X_OK)


@pytest.mark.skipif(path_utils.IS_WINDOWS, reason="Symlink replacement is POSIX-only")
def test_create_posix_symlink_replaces_existing(tmp_path: pathlib.Path) -> None:
    """POSIX shim generation replaces stale symlinks safely."""
    link = tmp_path / "ls"
    link.symlink_to(tmp_path / "missing")
    assert link.is_symlink()

    mapping = shimgen.create_shims(tmp_path, ["ls"])
    assert mapping["ls"].resolve() == shimgen.SHIM_PATH


@pytest.mark.parametrize(
    "name", ["../evil", "bad/name", "bad\\name", "..", "", "bad\x00name"]
)
def test_create_shims_invalid_command_name(name: str) -> None:
    """Invalid command names should raise ValueError."""
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        with pytest.raises(ValueError, match="Invalid command name"):
            shimgen.create_shims(env.shim_dir, [name])


def test_create_shims_detects_case_insensitive_duplicates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case-insensitive collisions should be rejected on Windows."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    with pytest.raises(ValueError, match="Duplicate command names"):
        shimgen.create_shims(tmp_path, ["git", "GIT"])


@pytest.mark.parametrize(
    ("validator", "bad_name"),
    [
        (_validate_not_empty, ""),
        (_validate_not_dot_directories, "."),
        (_validate_not_dot_directories, ".."),
        (_validate_no_path_separators, "bad/name"),
        (_validate_no_path_separators, "bad\\name"),
        (_validate_no_nul_bytes, "bad\x00name"),
    ],
)
def test_validators_raise_error(
    validator: cabc.Callable[[str, str], None], bad_name: str
) -> None:
    """Each validator rejects bad input."""
    with pytest.raises(ValueError, match="error"):
        validator(bad_name, "error")


def test_validators_accept_valid_name() -> None:
    """Validators accept well-formed names."""
    for validator in [
        _validate_not_empty,
        _validate_not_dot_directories,
        _validate_no_path_separators,
        _validate_no_nul_bytes,
    ]:
        validator("good", "error")


def test_create_shims_is_exported_from_the_package() -> None:
    """The bulk creator and its alias are exported with the same identity."""
    import cmd_mox

    assert cmd_mox.create_shims is shimgen.create_shims
    assert cmd_mox.create_shim_symlinks is shimgen.create_shim_symlinks


def test_create_shim_symlinks_alias_warns_and_creates(
    tmp_path: pathlib.Path,
) -> None:
    """The deprecated alias warns and delegates to :func:`create_shims`."""
    with pytest.warns(DeprecationWarning, match="create_shims"):
        mapping = shimgen.create_shim_symlinks(tmp_path, ["git"])

    _assert_is_shim(mapping["git"])


def test_ensure_shim_requires_existing_directory(tmp_path: pathlib.Path) -> None:
    """A missing shim directory is rejected before launcher work."""
    with pytest.raises(FileNotFoundError):
        shimgen.ensure_shim(tmp_path / "missing", "git")


def test_ensure_shim_requires_shim_template(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing shim template is rejected before destination inspection."""
    monkeypatch.setattr(shimgen, "SHIM_PATH", tmp_path / "missing-shim")

    with pytest.raises(FileNotFoundError, match="Shim template not found"):
        shimgen.ensure_shim(tmp_path, "git")


def test_ensure_shim_validates_command_name(tmp_path: pathlib.Path) -> None:
    """An unsafe command name is rejected before destination inspection."""
    with pytest.raises(ValueError, match="Invalid command name"):
        shimgen.ensure_shim(tmp_path, "../evil")


@pytest.mark.skipif(path_utils.IS_WINDOWS, reason="POSIX symlink semantics only")
def test_ensure_shim_creates_posix_symlink(tmp_path: pathlib.Path) -> None:
    """ensure_shim creates a symlink when no entry exists yet."""
    link = shimgen.ensure_shim(tmp_path, "git")

    assert link == tmp_path / "git"
    assert link.is_symlink()
    assert link.resolve() == shimgen.SHIM_PATH


@pytest.mark.skipif(path_utils.IS_WINDOWS, reason="POSIX symlink semantics only")
def test_ensure_shim_keeps_healthy_posix_symlink(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An existing symlink with a live target is reused without rewriting."""
    link = tmp_path / "git"
    link.symlink_to(shimgen.SHIM_PATH)

    def fail_create(directory: pathlib.Path, name: str) -> pathlib.Path:
        """Fail if ensure_shim tries to recreate a healthy link."""
        raise AssertionError(_HEALTHY_SYMLINK_REWRITE_MESSAGE)

    monkeypatch.setattr(shimgen, "_create_posix_symlink", fail_create)

    assert shimgen.ensure_shim(tmp_path, "git") == link
    assert link.resolve() == shimgen.SHIM_PATH


@pytest.mark.skipif(path_utils.IS_WINDOWS, reason="POSIX symlink semantics only")
def test_ensure_shim_repairs_broken_posix_symlink(tmp_path: pathlib.Path) -> None:
    """A dangling symlink is replaced with a link to the shim template."""
    link = tmp_path / "git"
    link.symlink_to(tmp_path / "missing")
    assert not link.exists()

    assert shimgen.ensure_shim(tmp_path, "git") == link
    assert link.resolve() == shimgen.SHIM_PATH


@pytest.mark.skipif(path_utils.IS_WINDOWS, reason="POSIX symlink semantics only")
def test_ensure_shim_rejects_posix_collision(tmp_path: pathlib.Path) -> None:
    """A non-symlink entry is reported and preserved."""
    collision = tmp_path / "git"
    collision.write_text("foreign")

    with pytest.raises(FileExistsError, match="already exists and is not a symlink"):
        shimgen.ensure_shim(tmp_path, "git")

    assert collision.read_text() == "foreign"


def test_ensure_shim_keeps_matching_windows_launcher(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A launcher matching the generator's bytes is reused unchanged."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    launcher = shimgen.ensure_shim(tmp_path, "git")
    original = launcher.read_bytes()

    def fail_create(directory: pathlib.Path, name: str) -> pathlib.Path:
        """Fail if ensure_shim tries to recreate a matching launcher."""
        raise AssertionError(_MATCHING_LAUNCHER_REWRITE_MESSAGE)

    monkeypatch.setattr(shimgen, "_create_windows_shim", fail_create)

    assert shimgen.ensure_shim(tmp_path, "git") == launcher
    assert launcher.read_bytes() == original


def test_ensure_shim_rejects_foreign_windows_launcher(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A foreign .cmd entry is reported and preserved byte for byte."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    launcher = tmp_path / "git.cmd"
    launcher.write_bytes(b"foreign")

    with pytest.raises(FileExistsError, match="not a cmd-mox launcher"):
        shimgen.ensure_shim(tmp_path, "git")

    assert launcher.read_bytes() == b"foreign"


def test_ensure_shim_chains_windows_read_error(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable launcher surfaces a chained collision error."""
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    launcher = tmp_path / "git.cmd"
    launcher.write_bytes(b"foreign")

    def fail_read_bytes(self: pathlib.Path) -> bytes:
        """Simulate a launcher the process cannot read."""
        raise OSError("unreadable")

    monkeypatch.setattr(pathlib.Path, "read_bytes", fail_read_bytes)

    with pytest.raises(FileExistsError, match="not a cmd-mox launcher") as excinfo:
        shimgen.ensure_shim(tmp_path, "git")

    assert isinstance(excinfo.value.__cause__, OSError)


@pytest.mark.skipif(path_utils.IS_WINDOWS, reason="POSIX symlink semantics only")
@pytest.mark.parametrize("target_kind", ["file", "directory"])
def test_ensure_shim_repairs_foreign_posix_symlink(
    tmp_path: pathlib.Path, target_kind: str
) -> None:
    """A symlink to a live foreign target is repaired, not treated as healthy."""
    target = tmp_path / f"foreign-{target_kind}"
    if target_kind == "directory":
        target.mkdir()
    else:
        target.write_text("foreign")

    link = tmp_path / "git"
    link.symlink_to(target)

    assert shimgen.ensure_shim(tmp_path, "git") == link
    assert link.resolve() == shimgen.SHIM_PATH
