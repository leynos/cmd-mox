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


def test_create_shim_symlinks_missing_target_dir() -> None:
    """Error raised when directory does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        missing = pathlib.Path(tmpdir) / "absent"
        with pytest.raises(FileNotFoundError):
            shimgen.create_shim_symlinks(missing, ["ls"])


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


@pytest.mark.parametrize(
    "name", ["../evil", "bad/name", "bad\\name", "..", "", "bad\x00name"]
)
def test_create_shim_symlinks_invalid_command_name(name: str) -> None:
    """Invalid command names should raise ValueError."""
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        with pytest.raises(ValueError, match="Invalid command name"):
            shimgen.create_shim_symlinks(env.shim_dir, [name])


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
