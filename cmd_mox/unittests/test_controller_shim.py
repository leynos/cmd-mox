"""Unit tests for :mod:`cmd_mox.controller` - shim creation and symlink management."""

from __future__ import annotations

import typing as t
from pathlib import Path

import pytest

import cmd_mox.controller as controller
from cmd_mox import _path_utils as path_utils
from cmd_mox.controller import CmdMox, Phase
from cmd_mox.errors import UnexpectedCommandError

pytestmark = [
    pytest.mark.requires_unix_sockets,
    pytest.mark.skipif(
        path_utils.IS_WINDOWS, reason="Windows uses batch shim launchers"
    ),
]

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    import subprocess


_SYMLINK_FAILURE_MESSAGE = "symlink failure"


class _ShimSymlinkSpy:
    """Capture shim creation attempts for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[Path, tuple[str, ...]]] = []

    def __call__(self, directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
        recorded = tuple(commands)
        self.calls.append((directory, recorded))
        return {name: directory / name for name in recorded}

    @property
    def called(self) -> bool:
        return bool(self.calls)


@pytest.fixture
def shim_symlink_spy(monkeypatch: pytest.MonkeyPatch) -> _ShimSymlinkSpy:
    """Redirect ``create_shim_symlinks`` to a spy for reuse across tests."""
    spy = _ShimSymlinkSpy()
    monkeypatch.setattr(controller, "create_shim_symlinks", spy)
    return spy


def test_cmdmox_nonstubbed_command_behavior(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Invoking a non-stubbed command returns name but fails verification."""
    mox = CmdMox()
    mox.register_command("not_stubbed")
    mox.__enter__()
    mox.replay()

    assert mox.environment.shim_dir is not None
    cmd_path = Path(mox.environment.shim_dir) / "not_stubbed"
    result = run([str(cmd_path)])

    assert result.stdout.strip() == "not_stubbed"

    with pytest.raises(UnexpectedCommandError):
        mox.verify()


def test_register_command_creates_shim_during_replay(
    shim_symlink_spy: _ShimSymlinkSpy,
) -> None:
    """register_command creates missing shims immediately during replay."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()
    shim_symlink_spy.calls.clear()

    mox.register_command("late")

    env = mox.environment
    assert env is not None
    assert env.shim_dir is not None
    assert shim_symlink_spy.calls == [(env.shim_dir, ("late",))]

    mox.verify()


def test_ensure_shim_during_replay_propagates_symlink_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shim creation failures bubble up so callers can surface them."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()

    def _boom(directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
        raise RuntimeError(_SYMLINK_FAILURE_MESSAGE)

    monkeypatch.setattr(controller, "create_shim_symlinks", _boom)

    with pytest.raises(RuntimeError, match=_SYMLINK_FAILURE_MESSAGE):
        mox._ensure_shim_during_replay("late")

    mox.__exit__(None, None, None)


@pytest.mark.parametrize(
    ("setup", "expected_call_count", "cleanup"),
    [
        pytest.param("no_replay", 0, None, id="outside-replay"),
        pytest.param("phase_only", 0, None, id="replay-without-environment"),
        pytest.param("full_replay", 1, "exit", id="replay-with-environment"),
    ],
)
def test_ensure_shim_during_replay_behaviour(
    setup: str,
    expected_call_count: int,
    cleanup: str | None,
    shim_symlink_spy: _ShimSymlinkSpy,
) -> None:
    """_ensure_shim_during_replay handles replay state and environment availability."""
    mox = CmdMox()

    if setup == "phase_only":
        # Directly toggle the private phase to isolate replay behaviour without
        # invoking the full environment machinery. The test accepts the tighter
        # coupling in exchange for targeting this edge case explicitly.
        mox._phase = Phase.REPLAY
    elif setup == "full_replay":
        mox.__enter__()
        mox.replay()
        shim_symlink_spy.calls.clear()

    mox._ensure_shim_during_replay("late")

    if expected_call_count:
        env = mox.environment
        assert env is not None
        assert env.shim_dir is not None
        assert shim_symlink_spy.calls == [(env.shim_dir, ("late",))]
    else:
        assert not shim_symlink_spy.called
        assert shim_symlink_spy.calls == []

    assert len(shim_symlink_spy.calls) == expected_call_count

    if cleanup == "exit":
        mox.__exit__(None, None, None)


def test_ensure_shim_during_replay_repairs_broken_symlink(
    tmp_path: Path, shim_symlink_spy: _ShimSymlinkSpy
) -> None:
    """Broken symlinks are recreated when replay is active."""
    mox = CmdMox()
    mox._phase = Phase.REPLAY
    env = mox.environment
    env.shim_dir = tmp_path

    shim_path = tmp_path / "late"
    shim_path.symlink_to(tmp_path / "missing")
    assert shim_path.is_symlink()
    assert not shim_path.exists()

    mox._ensure_shim_during_replay("late")

    assert shim_symlink_spy.calls == [(tmp_path, ("late",))]


def test_ensure_shim_during_replay_repairs_multiple_broken_symlinks(
    tmp_path: Path, shim_symlink_spy: _ShimSymlinkSpy
) -> None:
    """Each broken shim triggers an individual repair."""
    mox = CmdMox()
    mox._phase = Phase.REPLAY
    env = mox.environment
    env.shim_dir = tmp_path

    broken = {
        "first": tmp_path / "first-missing",
        "second": tmp_path / "second-missing",
    }
    for name, target in broken.items():
        shim_path = tmp_path / name
        shim_path.symlink_to(target)
        assert shim_path.is_symlink()
        assert not shim_path.exists()

    for name in broken:
        mox._ensure_shim_during_replay(name)

    assert shim_symlink_spy.calls == [
        (tmp_path, ("first",)),
        (tmp_path, ("second",)),
    ]


def test_ensure_shim_during_replay_rejects_non_symlink_collisions(
    tmp_path: Path,
) -> None:
    """A pre-existing file blocks shim repair to avoid data loss."""
    mox = CmdMox()
    mox._phase = Phase.REPLAY
    env = mox.environment
    env.shim_dir = tmp_path

    collision = tmp_path / "late"
    collision.write_text("collision")

    with pytest.raises(FileExistsError, match="already exists and is not a symlink"):
        mox._ensure_shim_during_replay("late")


def test_register_command_fails_when_path_exists() -> None:
    """register_command refuses to overwrite existing non-symlink files."""
    mox = CmdMox(verify_on_exit=False)
    mox.__enter__()
    mox.replay()

    env = mox.environment
    assert env is not None
    assert env.shim_dir is not None

    collision = env.shim_dir / "late"
    collision.write_text("collision")

    with pytest.raises(FileExistsError, match="already exists and is not a symlink"):
        mox.register_command("late")

    mox.__exit__(None, None, None)


def test_register_command_propagates_shim_creation_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """register_command surfaces errors from shim creation helpers."""
    mox = CmdMox(verify_on_exit=False)
    mox.__enter__()
    mox.replay()

    def _boom(directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
        raise PermissionError

    monkeypatch.setattr(controller, "create_shim_symlinks", _boom)

    with pytest.raises(PermissionError):
        mox.register_command("late")

    mox.__exit__(None, None, None)


def test_register_command_skips_existing_shim(monkeypatch: pytest.MonkeyPatch) -> None:
    """register_command avoids recreating an existing shim."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()

    env = mox.environment
    assert env is not None
    assert env.shim_dir is not None
    controller.create_shim_symlinks(env.shim_dir, ["again"])

    called = False

    def _fail(directory: Path, commands: t.Iterable[str]) -> dict[str, Path]:
        nonlocal called
        called = True
        return {name: directory / name for name in commands}

    monkeypatch.setattr(controller, "create_shim_symlinks", _fail)
    mox.register_command("again")

    assert not called

    mox.verify()
