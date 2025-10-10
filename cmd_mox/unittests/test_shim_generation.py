"""Tests for shim generation utilities."""

import os
import pathlib
import stat
import subprocess
import tempfile
import typing as t

import pytest

from cmd_mox.environment import (
    CMOX_IPC_SOCKET_ENV,
    CMOX_IPC_TIMEOUT_ENV,
    EnvironmentManager,
)
from cmd_mox.ipc import IPCServer
from cmd_mox.shimgen import (
    SHIM_PATH,
    _validate_no_nul_bytes,
    _validate_no_path_separators,
    _validate_not_dot_directories,
    _validate_not_empty,
    create_shim_symlinks,
)

pytestmark = pytest.mark.requires_unix_sockets


def test_create_shim_symlinks_and_execution(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Symlinks execute the shim and expose the invoked name."""
    commands = ["git", "curl"]
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        assert env.socket_path is not None
        with IPCServer(env.socket_path):
            mapping = create_shim_symlinks(env.shim_dir, commands)
            assert set(mapping) == set(commands)
            assert os.environ[CMOX_IPC_SOCKET_ENV] == str(env.socket_path)
            assert os.environ[CMOX_IPC_TIMEOUT_ENV] == "5.0"
            for cmd in commands:
                link = mapping[cmd]
                assert link.is_symlink()
                assert link.resolve() == SHIM_PATH
                assert os.access(link, os.X_OK)
                result = run([str(link)])
                assert result.stdout.strip() == cmd
                assert result.stderr == ""
                assert result.returncode == 0


def test_create_shim_symlinks_missing_target_dir() -> None:
    """Error raised when directory does not exist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        missing = pathlib.Path(tmpdir) / "absent"
        with pytest.raises(FileNotFoundError):
            create_shim_symlinks(missing, ["ls"])


def test_create_shim_symlinks_existing_non_symlink_file() -> None:
    """Error raised when a non-symlink file already exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        path = pathlib.Path(tmpdir)
        file_path = path / "ls"
        file_path.write_text("not a symlink")
        with pytest.raises(FileExistsError):
            create_shim_symlinks(path, ["ls"])


def test_create_shim_symlinks_missing_or_non_executable_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Handle missing or non-executable shim templates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tempdir = pathlib.Path(tmpdir)
        missing_shim = tempdir / "missing"
        monkeypatch.setattr("cmd_mox.shimgen.SHIM_PATH", missing_shim)
        with pytest.raises(FileNotFoundError):
            create_shim_symlinks(tempdir, ["ls"])

        shim_path = tempdir / "fake_shim"
        shim_path.write_text("#!/bin/sh\necho fake")
        shim_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        monkeypatch.setattr("cmd_mox.shimgen.SHIM_PATH", shim_path)
        mapping = create_shim_symlinks(tempdir, ["ls"])
        assert mapping["ls"].is_symlink()
        assert os.access(shim_path, os.X_OK)


@pytest.mark.parametrize(
    "name", ["../evil", "bad/name", "bad\\name", "..", "", "bad\x00name"]
)
def test_create_shim_symlinks_invalid_command_name(name: str) -> None:
    """Invalid command names should raise ValueError."""
    with EnvironmentManager() as env:
        assert env.shim_dir is not None
        with pytest.raises(ValueError, match="Invalid command name"):
            create_shim_symlinks(env.shim_dir, [name])


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
