"""Tests for shim bootstrap and import-path setup."""

from __future__ import annotations

import os
import sys
import typing as typ

import pytest

from cmd_mox import shim
from cmd_mox._shim_bootstrap import _should_remove_path_entry
from cmd_mox.ipc import Invocation, Response

if typ.TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def symlink_cycle(tmp_path: Path) -> Path:
    """Provide a path whose symlinks point at each other.

    Returns
    -------
    pathlib.Path
        The first link of a two-link cycle, which no lookup can follow.
    """
    if not hasattr(os, "symlink"):
        pytest.skip("Platform does not support symlinks")
    first = tmp_path / "cycle-a"
    second = tmp_path / "cycle-b"
    try:
        first.symlink_to(second)
        second.symlink_to(first)
    except OSError as exc:  # pragma: no cover - windows without admin rights
        pytest.skip(f"Symlinks unavailable: {exc}")
    return first


def test_main_bootstraps_and_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shim entrypoint should bootstrap and delegate in order."""
    calls: list[object] = []
    monkeypatch.setattr(shim.time, "monotonic", lambda: 100.0)

    monkeypatch.setattr(shim, "bootstrap_shim_path", lambda: calls.append("bootstrap"))
    monkeypatch.setattr(
        shim, "_resolve_command_name", lambda: calls.append("resolve") or "shim"
    )
    monkeypatch.setattr(
        shim, "_validate_environment", lambda: calls.append("validate") or 1.0
    )

    invocation = Invocation(
        command="shim", args=[], stdin="", env={}, invocation_id="bootstrap-id"
    )
    monkeypatch.setattr(
        shim,
        "_create_invocation",
        lambda name, timeout, *, deadline: (
            calls.append(("create", name, timeout, deadline)) or invocation
        ),
    )

    response = Response(stdout="ok", stderr="", exit_code=0)
    monkeypatch.setattr(
        shim,
        "_execute_invocation",
        lambda inv, timeout, *, deadline: (
            calls.append(("execute", inv, timeout, deadline)) or response
        ),
    )
    monkeypatch.setattr(
        shim,
        "_write_response",
        lambda resp, *, correlation_id=None: calls.append((
            "write",
            resp,
            correlation_id,
        )),
    )

    shim.main()

    assert calls == [
        "bootstrap",
        "resolve",
        "validate",
        (
            "create",
            "shim",
            1.0,
            None if shim.path_utils.IS_WINDOWS else 101.0,
        ),
        (
            "execute",
            invocation,
            1.0,
            None if shim.path_utils.IS_WINDOWS else 101.0,
        ),
        ("write", response, "bootstrap-id"),
    ], "shim startup steps did not run in the expected order"


def test_bootstrap_shim_path_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling bootstrap_shim_path repeatedly should be safe and stable."""
    from cmd_mox import _shim_bootstrap

    monkeypatch.setattr(_shim_bootstrap, "_BOOTSTRAP_DONE", False)
    monkeypatch.setattr(sys, "path", ["__editable__dummy", "/usr/lib/python3.12"])

    _shim_bootstrap.bootstrap_shim_path()
    path_after_first = list(sys.path)

    _shim_bootstrap.bootstrap_shim_path()

    assert sys.path == path_after_first, "repeated bootstrap changed sys.path"
    assert sys.modules["platform"].__name__ == "platform", (
        "bootstrap did not leave the standard-library platform module loaded"
    )


def test_bootstrap_shim_path_prefers_stdlib_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Bootstrapping should discard editable paths when importing stdlib modules."""
    import importlib

    from cmd_mox import _shim_bootstrap

    monkeypatch.chdir(tmp_path)
    editable_dir = tmp_path / "__editable__site"
    editable_dir.mkdir()
    (editable_dir / "platform.py").write_text("MARKER = 'fake'\n")
    monkeypatch.setattr(sys, "path", ["__editable__site", "/usr/lib/python3.12"])
    monkeypatch.setattr(_shim_bootstrap, "_BOOTSTRAP_DONE", False)

    monkeypatch.delitem(sys.modules, "platform", raising=False)
    fake_platform = typ.cast("typ.Any", importlib.import_module("platform"))
    assert fake_platform.MARKER == "fake", (
        "test setup did not import the editable module"
    )

    _shim_bootstrap.bootstrap_shim_path()

    std_platform = sys.modules["platform"]
    assert not hasattr(std_platform, "MARKER"), (
        "bootstrap retained the editable platform module"
    )
    assert "__editable__site" in sys.path, (
        "bootstrap removed the editable application path"
    )


def test_bootstrap_shim_path_restores_sys_path_when_platform_load_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrapping should restore removed entries when platform loading fails."""
    from cmd_mox import _shim_bootstrap

    original_path = ["__editable__dummy", "/usr/lib/python3.12"]
    monkeypatch.setattr(_shim_bootstrap, "_BOOTSTRAP_DONE", False)
    monkeypatch.setattr(sys, "path", list(original_path))

    def raise_runtime_error() -> typ.NoReturn:
        """Fail platform loading to exercise bootstrap path restoration.

        Raises
        ------
        RuntimeError
            Simulates a platform-loader failure.
        """
        msg = "platform load failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(_shim_bootstrap, "_load_stdlib_platform", raise_runtime_error)

    with pytest.raises(RuntimeError, match="platform load failed"):
        _shim_bootstrap.bootstrap_shim_path()

    assert sys.path == original_path, "failed bootstrap did not restore sys.path"
    assert not _shim_bootstrap._BOOTSTRAP_DONE, (
        "failed bootstrap marked itself complete"
    )


def test_should_remove_path_entry_removes_package_dir_entry(tmp_path: Path) -> None:
    """An entry resolving to the package directory should be removed."""
    package_dir = tmp_path / "cmd_mox"
    package_dir.mkdir()

    assert _should_remove_path_entry(os.fspath(package_dir), package_dir), (
        "bootstrap kept the package directory on sys.path"
    )


def test_should_remove_path_entry_skips_entry_when_cwd_is_gone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative entry cannot be resolved once the working directory is gone."""
    package_dir = tmp_path / "cmd_mox"
    package_dir.mkdir()
    doomed_cwd = tmp_path / "doomed"
    doomed_cwd.mkdir()
    monkeypatch.chdir(doomed_cwd)
    doomed_cwd.rmdir()

    assert not _should_remove_path_entry(".", package_dir), (
        "bootstrap removed an entry it could not resolve"
    )


def test_should_remove_path_entry_skips_symlink_cycle(
    symlink_cycle: Path, tmp_path: Path
) -> None:
    """A malformed entry looping through symlinks must not abort bootstrap."""
    package_dir = tmp_path / "cmd_mox"
    package_dir.mkdir()

    assert not _should_remove_path_entry(os.fspath(symlink_cycle), package_dir), (
        "bootstrap removed an entry it could not resolve"
    )


def test_should_remove_path_entry_skips_entry_with_embedded_null(
    tmp_path: Path,
) -> None:
    """An entry the filesystem cannot interpret must not abort bootstrap."""
    package_dir = tmp_path / "cmd_mox"
    package_dir.mkdir()

    assert not _should_remove_path_entry("bad\x00entry", package_dir), (
        "bootstrap removed an entry it could not resolve"
    )
