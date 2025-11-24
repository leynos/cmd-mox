"""Unit tests covering shim generation for POSIX and Windows platforms."""

from __future__ import annotations

import os
import pathlib
import stat
import sys
import tempfile
import typing as t

import pytest

import cmd_mox.shimgen as shimgen
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

if t.TYPE_CHECKING:  # pragma: no cover - typing helpers only
    import subprocess


def _assert_is_shim(path: pathlib.Path) -> None:
    """Assert that *path* points to a usable shim implementation."""
    if shimgen.IS_WINDOWS:
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
    monkeypatch.setattr("cmd_mox.shimgen.IS_WINDOWS", False)
    _validate_command_uniqueness(["git", "GIT"])

    monkeypatch.setattr("cmd_mox.shimgen.IS_WINDOWS", True)
    with pytest.raises(ValueError, match="Duplicate command names"):
        _validate_command_uniqueness(["git", "GIT"])


@pytest.mark.requires_unix_sockets
@pytest.mark.skipif(shimgen.IS_WINDOWS, reason="POSIX symlink execution only")
def test_create_shim_symlinks_and_execution(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """POSIX shims execute the shared shim and expose the command name."""
    commands = ["git", "curl"]
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        assert env.socket_path is not None
        with IPCServer(env.socket_path):
            mapping = shimgen.create_shim_symlinks(env.shim_dir, commands)
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
    if not shimgen.IS_WINDOWS:
        monkeypatch.setattr("cmd_mox.shimgen.IS_WINDOWS", True)

    mapping = shimgen.create_shim_symlinks(tmp_path, ["git"])
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
    monkeypatch.setattr("cmd_mox.shimgen.IS_WINDOWS", True)
    mapping = shimgen.create_shim_symlinks(tmp_path, ["git"])
    launcher = mapping["git"]
    assert b"\r\n" in launcher.read_bytes()


def test_create_windows_shim_retries_locked_file(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Launcher creation retries when Windows reports transient locks."""
    monkeypatch.setattr("cmd_mox.shimgen.IS_WINDOWS", True)
    launcher = tmp_path / "git.cmd"
    launcher.write_text("old")

    original_unlink = pathlib.Path.unlink
    attempts: list[int] = [0]

    def flaky_unlink(self: pathlib.Path) -> None:
        if self == launcher and attempts[0] < 2:
            attempts[0] += 1
            raise PermissionError("locked")
        attempts[0] += 1
        original_unlink(self)

    monkeypatch.setattr(pathlib.Path, "unlink", flaky_unlink)
    sleeps: list[float] = []
    monkeypatch.setattr("cmd_mox.shimgen.time.sleep", sleeps.append)

    mapping = shimgen.create_shim_symlinks(tmp_path, ["git"])
    assert mapping["git"].exists()
    assert attempts[0] == 3
    assert sleeps == [0.5, 0.5]


def test_create_windows_shim_raises_after_retries(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exhausting retries surfaces a clear ``FileExistsError``."""
    monkeypatch.setattr("cmd_mox.shimgen.IS_WINDOWS", True)
    launcher = tmp_path / "git.cmd"
    launcher.write_text("old")

    def locked_unlink(self: pathlib.Path) -> None:
        raise PermissionError("locked")

    monkeypatch.setattr(pathlib.Path, "unlink", locked_unlink)
    monkeypatch.setattr("cmd_mox.shimgen.time.sleep", lambda _duration: None)

    with pytest.raises(FileExistsError, match="Failed to remove existing launcher"):
        shimgen.create_shim_symlinks(tmp_path, ["git"])


def test_create_shim_symlinks_missing_target_dir() -> None:
    """Error raised when directory does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        missing = pathlib.Path(tmpdir) / "absent"
        with pytest.raises(FileNotFoundError):
            shimgen.create_shim_symlinks(missing, ["ls"])


def test_create_shim_symlinks_rejects_non_directory(tmp_path: pathlib.Path) -> None:
    """Non-directory shim paths raise ``FileNotFoundError`` with context."""
    file_path = tmp_path / "not-a-dir"
    file_path.write_text("content")
    with pytest.raises(FileNotFoundError, match="not a directory"):
        shimgen.create_shim_symlinks(file_path, ["ls"])


def test_create_shim_symlinks_existing_non_symlink_file() -> None:
    """Error raised when an existing file collides with the shim name."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = pathlib.Path(tmpdir)
        file_name = "ls.cmd" if shimgen.IS_WINDOWS else "ls"
        file_path = path / file_name
        file_path.write_text("not a shim")
        with pytest.raises(FileExistsError):
            shimgen.create_shim_symlinks(path, ["ls"])


def test_create_shim_symlinks_missing_or_non_executable_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle missing or non-executable shim templates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tempdir = pathlib.Path(tmpdir)
        missing_shim = tempdir / "missing"
        monkeypatch.setattr("cmd_mox.shimgen.SHIM_PATH", missing_shim)
        with pytest.raises(FileNotFoundError):
            shimgen.create_shim_symlinks(tempdir, ["ls"])

        shim_path = tempdir / "fake_shim"
        shim_path.write_text("#!/bin/sh\necho fake")
        shim_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        monkeypatch.setattr("cmd_mox.shimgen.SHIM_PATH", shim_path)
        mapping = shimgen.create_shim_symlinks(tempdir, ["ls"])
        _assert_is_shim(mapping["ls"])
        assert os.access(shim_path, os.X_OK)


@pytest.mark.skipif(shimgen.IS_WINDOWS, reason="Symlink replacement is POSIX-only")
def test_create_posix_symlink_replaces_existing(tmp_path: pathlib.Path) -> None:
    """POSIX shim generation replaces stale symlinks safely."""
    link = tmp_path / "ls"
    link.symlink_to(tmp_path / "missing")
    assert link.is_symlink()

    mapping = shimgen.create_shim_symlinks(tmp_path, ["ls"])
    assert mapping["ls"].resolve() == shimgen.SHIM_PATH


@pytest.mark.parametrize(
    "name", ["../evil", "bad/name", "bad\\name", "..", "", "bad\x00name"]
)
def test_create_shim_symlinks_invalid_command_name(name: str) -> None:
    """Invalid command names should raise ValueError."""
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        with pytest.raises(ValueError, match="Invalid command name"):
            shimgen.create_shim_symlinks(env.shim_dir, [name])


def test_create_shim_symlinks_detects_case_insensitive_duplicates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Case-insensitive collisions should be rejected on Windows."""
    monkeypatch.setattr("cmd_mox.shimgen.IS_WINDOWS", True)
    with pytest.raises(ValueError, match="Duplicate command names"):
        shimgen.create_shim_symlinks(tmp_path, ["git", "GIT"])


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
    validator: t.Callable[[str, str], None], bad_name: str
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
