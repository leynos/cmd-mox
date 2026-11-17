"""Unit tests for :mod:`cmd_mox.controller` - shim creation and health."""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox import controller, shimgen
from cmd_mox.controller import CmdMox, Phase
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.unittests._env_helpers import require_shim_dir, shim_path

pytestmark = [
    pytest.mark.requires_unix_sockets,
    pytest.mark.skipif(
        path_utils.IS_WINDOWS, reason="Windows uses batch shim launchers"
    ),
]

if typ.TYPE_CHECKING:  # pragma: no cover - typing only
    import collections.abc as cabc
    import subprocess
    from pathlib import Path


_SYMLINK_FAILURE_MESSAGE = "symlink failure"
_HEALTHY_SHIM_REWRITE_MESSAGE = "healthy shim must not be recreated"


class _EnsureShimSpy:
    """Capture ``ensure_shim`` calls for assertions."""

    def __init__(self) -> None:
        """Start with no recorded shim calls."""
        self.calls: list[tuple[Path, str]] = []

    def __call__(self, directory: Path, name: str) -> Path:
        """Record the call and return the expected launcher path."""
        self.calls.append((directory, name))
        return directory / name

    @property
    def called(self) -> bool:
        """Whether any shim call has been recorded."""
        return bool(self.calls)


@pytest.fixture
def ensure_shim_spy(monkeypatch: pytest.MonkeyPatch) -> _EnsureShimSpy:
    """Redirect ``ensure_shim`` to a spy for reuse across tests.

    Returns
    -------
    _EnsureShimSpy
        The spy installed as the shim creation function.
    """
    spy = _EnsureShimSpy()
    monkeypatch.setattr(controller, "ensure_shim", spy)
    return spy


def _assert_shim_created_for(spy: _EnsureShimSpy, mox: CmdMox, name: str) -> None:
    """Assert the spy recorded exactly one shim creation for *name*.

    Parameters
    ----------
    spy : _EnsureShimSpy
        Spy standing in for ``ensure_shim``.
    mox : CmdMox
        Controller whose environment supplies the expected shim directory.
    name : str
        Command name that should have been created.
    """
    env = mox.environment
    assert env is not None
    assert env.shim_dir is not None
    assert spy.calls == [(env.shim_dir, name)]


def test_cmdmox_nonstubbed_command_behavior(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Invoking a non-stubbed command returns name but fails verification."""
    mox = CmdMox()
    mox.register_command("not_stubbed")
    mox.__enter__()
    mox.replay()

    cmd_path = shim_path(mox.environment, "not_stubbed")
    result = run([str(cmd_path)])

    assert result.stdout.strip() == "not_stubbed"

    with pytest.raises(UnexpectedCommandError):
        mox.verify()


def test_register_command_creates_shim_during_replay(
    ensure_shim_spy: _EnsureShimSpy,
) -> None:
    """register_command creates missing shims immediately during replay."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()
    ensure_shim_spy.calls.clear()

    mox.register_command("late")

    _assert_shim_created_for(ensure_shim_spy, mox, "late")

    mox.verify()


def test_register_command_rejects_case_variant_during_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A late case-variant registration leaves no command or double behind."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    mox.register_command("git")

    with pytest.raises(ValueError, match="Duplicate command names"):
        mox.stub("GIT")

    assert "GIT" not in mox._commands
    assert "GIT" not in mox._doubles

    mox.verify()


def test_register_command_accepts_repeated_name(
    ensure_shim_spy: _EnsureShimSpy,
) -> None:
    """Repeating an exactly-named registration stays idempotent."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()
    ensure_shim_spy.calls.clear()

    mox.register_command("late")
    mox.register_command("late")

    assert [name for _, name in ensure_shim_spy.calls] == ["late", "late"]

    mox.verify()


def test_double_is_published_before_shim_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new double is visible while its late shim is being created."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()

    def assert_published(directory: Path, name: str) -> Path:
        """Fail unless the double is already available to the IPC server."""
        assert name in mox._doubles
        return directory / name

    monkeypatch.setattr(controller, "ensure_shim", assert_published)

    mox.stub("late")

    assert "late" in mox._doubles

    mox.verify()


def test_ensure_shim_during_replay_propagates_symlink_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Shim creation failures bubble up so callers can surface them."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()

    def _boom(directory: Path, name: str) -> Path:
        """Raise a RuntimeError to simulate a failed symlink creation."""
        raise RuntimeError(_SYMLINK_FAILURE_MESSAGE)

    monkeypatch.setattr(controller, "ensure_shim", _boom)

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
    ensure_shim_spy: _EnsureShimSpy,
) -> None:
    """_ensure_shim_during_replay handles replay state and environment availability."""
    mox = CmdMox()

    match setup:
        case "phase_only":
            # Directly toggle the private phase to isolate replay behaviour
            # without invoking the full environment machinery. The test accepts
            # the tighter coupling in exchange for targeting this edge case
            # explicitly.
            mox._phase = Phase.REPLAY
        case "full_replay":
            mox.__enter__()
            mox.replay()
            ensure_shim_spy.calls.clear()
        case "no_replay":
            # Leave the controller in its pristine record phase.
            pass
        case _:
            # Guard against a future typo or an unsupported case being added to
            # the parametrization and silently exercising the no-replay path.
            pytest.fail(f"unsupported setup: {setup!r}")

    mox._ensure_shim_during_replay("late")

    if expected_call_count:
        _assert_shim_created_for(ensure_shim_spy, mox, "late")
    else:
        assert not ensure_shim_spy.called
        assert ensure_shim_spy.calls == []

    assert len(ensure_shim_spy.calls) == expected_call_count

    if cleanup == "exit":
        mox.__exit__(None, None, None)


def test_ensure_shim_during_replay_repairs_broken_symlink(
    tmp_path: Path,
) -> None:
    """Broken symlinks are recreated when replay is active."""
    mox = CmdMox()
    mox._phase = Phase.REPLAY
    env = mox.environment
    env.shim_dir = tmp_path

    link = tmp_path / "late"
    link.symlink_to(tmp_path / "missing")
    assert link.is_symlink()
    assert not link.exists()

    mox._ensure_shim_during_replay("late")

    assert link.is_symlink()
    assert link.resolve() == shimgen.SHIM_PATH


def test_ensure_shim_during_replay_repairs_multiple_broken_symlinks(
    tmp_path: Path,
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
        link = tmp_path / name
        link.symlink_to(target)
        assert link.is_symlink()
        assert not link.exists()

    for name in broken:
        mox._ensure_shim_during_replay(name)

    for name in broken:
        assert (tmp_path / name).resolve() == shimgen.SHIM_PATH


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

    collision = shim_path(mox.environment, "late")
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

    def _boom(directory: Path, name: str) -> Path:
        """Raise PermissionError to simulate an unwritable shim directory."""
        raise PermissionError

    monkeypatch.setattr(controller, "ensure_shim", _boom)

    with pytest.raises(PermissionError):
        mox.register_command("late")

    mox.__exit__(None, None, None)


def test_register_command_keeps_existing_healthy_shim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """register_command leaves an existing healthy shim untouched."""
    mox = CmdMox()
    mox.__enter__()
    mox.replay()

    link = controller.ensure_shim(require_shim_dir(mox.environment), "again")

    def fail_create(directory: Path, name: str) -> Path:
        """Fail if a healthy shim is recreated."""
        raise AssertionError(_HEALTHY_SHIM_REWRITE_MESSAGE)

    monkeypatch.setattr(shimgen, "_create_posix_symlink", fail_create)
    mox.register_command("again")

    assert link.resolve() == shimgen.SHIM_PATH

    mox.verify()


def test_failed_registration_does_not_block_case_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed registration is rolled back, so a later variant can register."""
    mox = CmdMox(verify_on_exit=False)
    mox.__enter__()
    mox.replay()
    monkeypatch.setattr(path_utils, "IS_WINDOWS", True)
    shim_dir = require_shim_dir(mox.environment)
    foreign = shim_dir / "git.cmd"
    foreign.write_bytes(b"foreign")

    with pytest.raises(FileExistsError, match="not a cmd-mox launcher"):
        mox.stub("git")

    assert "git" not in mox._commands
    assert "git" not in mox._doubles

    foreign.unlink()
    mox.stub("GIT")
    assert "GIT" in mox._doubles

    mox.__exit__(None, None, None)
