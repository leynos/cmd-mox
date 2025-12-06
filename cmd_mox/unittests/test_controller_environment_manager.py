"""Unit tests for :mod:`cmd_mox.controller` - environment manager error handling."""

from __future__ import annotations

import typing as t
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.environment import EnvironmentManager
from cmd_mox.errors import MissingEnvironmentError

pytestmark = pytest.mark.requires_unix_sockets


def test_start_ipc_server_requires_environment() -> None:
    """Starting the IPC server without an environment raises an error."""
    mox = CmdMox()
    with pytest.raises(MissingEnvironmentError, match="not initialised"):
        mox._start_ipc_server()


@pytest.mark.parametrize("attr_name", ["shim_dir", "socket_path"])
def test_cmdmox_replay_fails_when_attr_missing(
    monkeypatch: pytest.MonkeyPatch, attr_name: str
) -> None:
    """Replay fails when the specified environment attribute is missing."""
    mox = CmdMox()
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()

    monkeypatch.setattr(mox.environment, attr_name, None)
    expected = {
        "shim_dir": "Replay shim directory is missing",
        "socket_path": "Replay socket path is missing",
    }[attr_name]

    with pytest.raises(MissingEnvironmentError, match=expected):
        mox.replay()

    # Use the public context-manager API to restore PATH and other state.
    # Calling the private _stop_server_and_exit_env helper would bypass
    # type checking, so tests rely on __exit__ instead.
    mox.__exit__(None, None, None)


def test_cmdmox_replay_reports_all_missing_attrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay surfaces every missing EnvironmentManager attribute."""
    mox = CmdMox()
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()

    monkeypatch.setattr(mox.environment, "shim_dir", None)
    monkeypatch.setattr(mox.environment, "socket_path", None)
    with pytest.raises(
        MissingEnvironmentError,
        match=r"Replay shim directory.*Replay socket path",
    ):
        mox.replay()

    # Use the public context-manager API to restore PATH and other state.
    mox.__exit__(None, None, None)


def test_verify_missing_environment_attributes(monkeypatch: pytest.MonkeyPatch) -> None:
    """verify() fails when environment attributes are missing."""
    mox = CmdMox(
        verify_on_exit=False
    )  # Disable auto-verify (normally called by __exit__) to avoid double error
    mox.stub("foo").returns(stdout="bar")
    mox.__enter__()  # Manual context entry keeps the cleanup path explicit.
    mox.replay()

    # Monkeypatch after entering the context but before verify() runs.
    monkeypatch.setattr(mox.environment, "shim_dir", None)
    monkeypatch.setattr(mox.environment, "socket_path", None)
    with pytest.raises(
        MissingEnvironmentError,
        match=r"Replay shim directory.*Replay socket path",
    ):
        mox.verify()
    mox.__exit__(None, None, None)


def test_replay_detects_environment_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replay raises MissingEnvironmentError if shims vanish mid-start."""
    mox = CmdMox()
    env = mox.environment
    assert env is not None

    original_check = mox._check_replay_preconditions

    def tampered() -> None:
        original_check()
        env.shim_dir = None
        env.socket_path = None

    monkeypatch.setattr(mox, "_check_replay_preconditions", tampered)

    with (
        pytest.raises(
            MissingEnvironmentError, match="Replay shim directory is missing"
        ),
        mox,
    ):
        mox.replay()


def test_require_env_attrs_rejects_missing_environment() -> None:
    """_require_env_attrs surfaces the default missing environment message."""
    mox = CmdMox()
    mox.environment = None
    with pytest.raises(
        MissingEnvironmentError, match="Replay environment is not ready"
    ):
        mox._require_env_attrs("shim_dir")


def test_validate_replay_environment_handles_missing_and_file_paths(
    tmp_path: Path,
) -> None:
    """_validate_replay_environment reports missing and non-directory shim paths."""
    mox = CmdMox()
    env = EnvironmentManager()
    mox.environment = env

    env.shim_dir = tmp_path / "absent"
    env.socket_path = tmp_path / "ipc.sock"
    with pytest.raises(
        MissingEnvironmentError,
        match="Replay shim directory does not exist",
    ):
        mox._validate_replay_environment()

    file_path = tmp_path / "not_a_dir"
    file_path.write_text("content")
    env.shim_dir = file_path
    with pytest.raises(
        MissingEnvironmentError,
        match="Replay shim directory is not a directory",
    ):
        mox._validate_replay_environment()


def test_validate_replay_environment_missing_socket(tmp_path: Path) -> None:
    """Missing socket path is reported with a normalised message."""
    mox = CmdMox()
    env = EnvironmentManager()
    env.shim_dir = tmp_path
    env.socket_path = None
    mox.environment = env

    with pytest.raises(MissingEnvironmentError, match="Replay socket path is missing"):
        mox._validate_replay_environment()


def test_validate_replay_environment_success(tmp_path: Path) -> None:
    """Returns Path objects when both paths are valid."""
    mox = CmdMox()
    env = EnvironmentManager()
    env.shim_dir = tmp_path
    env.socket_path = tmp_path / "ipc.sock"
    mox.environment = env

    shim_dir, socket_path = mox._validate_replay_environment()
    assert shim_dir == tmp_path
    assert socket_path == env.socket_path


@pytest.mark.parametrize(
    ("setup_invalid_path", "expected_error"),
    [
        (
            lambda base: _create_file(base / "not_a_dir"),
            "Replay shim directory is not a directory",
        ),
        (
            lambda base: base / "missing",
            "Replay shim directory does not exist",
        ),
    ],
    ids=["file_instead_of_directory", "missing_on_disk"],
)
def test_replay_fails_when_shim_dir_is_invalid(
    setup_invalid_path: t.Callable[[Path], Path], expected_error: str
) -> None:
    """Replay rejects shim_dir paths that are not usable directories."""
    mox = CmdMox()
    with mox:
        env = mox.environment
        assert env is not None
        assert env.shim_dir is not None

        invalid = setup_invalid_path(Path(env.shim_dir))
        env.shim_dir = invalid

        with pytest.raises(MissingEnvironmentError, match=expected_error):
            mox.replay()


def _create_file(path: Path) -> Path:
    path.write_text("content")
    return path
