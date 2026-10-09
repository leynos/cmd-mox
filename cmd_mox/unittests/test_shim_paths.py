"""Tests for shim argument and executable path resolution."""

from __future__ import annotations

import os
import sys
import typing as typ
from pathlib import Path

import pytest

from cmd_mox import shim
from cmd_mox.command_runner import validate_override_path as _validate_override_path
from cmd_mox.environment import CMOX_IPC_SOCKET_ENV, CMOX_REAL_COMMAND_ENV_PREFIX
from cmd_mox.ipc import Invocation, PassthroughRequest, Response
from cmd_mox.shim import _merge_passthrough_path, _resolve_passthrough_target
from cmd_mox.unittests.test_shim_support import _DummyStdin

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@pytest.fixture
def directory_symlink(tmp_path: Path) -> Path:
    """Provide a directory symlink for override validation tests.

    Returns
    -------
    pathlib.Path
        The symlink to a directory.
    """
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    symlink = tmp_path / "dir-link"
    if not hasattr(os, "symlink"):
        pytest.skip("Platform does not support symlinks")
    try:
        symlink.symlink_to(target_dir, target_is_directory=True)
    except OSError as exc:  # pragma: no cover - windows without admin rights
        pytest.skip(f"Symlinks unavailable: {exc}")
    return symlink


@pytest.mark.parametrize(
    ("is_windows", "expected"),
    [
        (True, r"^literal^"),
        (False, r"^^^literal^^^^"),
    ],
    ids=["windows-collapses-carets", "posix-preserves-carets"],
)
def test_normalize_windows_arg_respects_platform(
    monkeypatch: pytest.MonkeyPatch,
    is_windows: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - parametrized platform, not a flag
    expected: str,
) -> None:
    """Caret normalisation should only collapse carets on Windows."""
    monkeypatch.setattr("cmd_mox._path_utils.IS_WINDOWS", is_windows)

    assert shim._normalize_windows_arg(r"^^^literal^^^^") == expected


def test_create_invocation_normalizes_windows_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invocation creation should collapse doubled carets on Windows."""
    monkeypatch.setattr("cmd_mox._path_utils.IS_WINDOWS", True)
    monkeypatch.setattr(sys, "argv", ["shim", r"foo^^^bar", r"arg^^^^"])
    monkeypatch.setenv("EXTRA", "1")
    monkeypatch.setattr(sys, "stdin", _DummyStdin("ignored", is_tty=True))

    invocation = shim._create_invocation("shim", timeout=1.0)

    assert invocation.args == [r"foo^bar", r"arg^"]


def test_build_search_path_posix_merges_with_colon_pathsep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_build_search_path should merge entries correctly on POSIX."""
    monkeypatch.setattr(shim.os, "pathsep", ":")
    monkeypatch.setattr("cmd_mox._path_utils.IS_WINDOWS", False)

    merged_path = " :/opt/bin::/custom/bin: "
    lookup_path = " :/usr/local/bin::/usr/bin: "

    result = shim._build_search_path(merged_path, lookup_path, shim_dir=None)

    assert result == ":".join(["/opt/bin", "/custom/bin", "/usr/local/bin", "/usr/bin"])


def test_build_search_path_trims_and_preserves_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace and empty entries should be removed without reordering."""
    monkeypatch.setattr(shim.os, "pathsep", ":")
    monkeypatch.setattr("cmd_mox._path_utils.IS_WINDOWS", False)

    merged_path = " :/usr/local/bin::/custom/bin: /another/bin :"

    result = shim._build_search_path(merged_path, "", shim_dir=None)

    assert result.split(":") == [
        "/usr/local/bin",
        "/custom/bin",
        "/another/bin",
    ]


def test_build_search_path_filters_shim_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Entries matching shim_dir should be removed from the merged PATH."""
    shim_dir = tmp_path / "shim"
    monkeypatch.setattr("cmd_mox._path_utils.IS_WINDOWS", False)
    merged_path = os.pathsep.join([os.fspath(shim_dir), "/usr/local/bin"])
    lookup_path = os.pathsep.join(["/custom/bin", os.fspath(shim_dir)])

    result = shim._build_search_path(merged_path, lookup_path, shim_dir=shim_dir)

    assert result.split(os.pathsep) == ["/usr/local/bin", "/custom/bin"]


def test_build_search_path_handles_missing_env_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """None or empty merged_path should fall back to lookup_path cleanly."""
    monkeypatch.setattr("cmd_mox._path_utils.IS_WINDOWS", False)
    lookup_path = os.pathsep.join(["/bin", "/usr/bin"])

    assert shim._build_search_path(None, lookup_path, shim_dir=None) == lookup_path
    assert shim._build_search_path("", lookup_path, shim_dir=None) == lookup_path


@pytest.mark.parametrize(
    ("factory", "expected_exit", "expected_message"),
    [
        (
            lambda tmp_path: tmp_path / "missing",  # missing file
            127,
            "not found",
        ),
        (
            lambda tmp_path: tmp_path,  # directory
            126,
            "invalid executable path",
        ),
    ],
    ids=["missing-file", "directory"],
)
def test_validate_override_path_reports_missing_or_invalid_targets(
    tmp_path: Path,
    factory: cabc.Callable[[Path], Path],
    expected_exit: int,
    expected_message: str,
) -> None:
    """Validate error handling for nonexistent and non-file overrides."""
    target = factory(tmp_path)
    result = _validate_override_path("tool", os.fspath(target))

    assert isinstance(result, Response)
    assert result.exit_code == expected_exit
    assert expected_message in result.stderr


def test_validate_override_path_rejects_directory_symlink(
    directory_symlink: Path,
) -> None:
    """Symlinks pointing at directories should be rejected as executables."""
    result = _validate_override_path("tool", os.fspath(directory_symlink))

    assert isinstance(result, Response)
    assert result.exit_code == 126
    assert "invalid executable path" in result.stderr


def test_validate_override_path_rejects_non_executable_file(tmp_path: Path) -> None:
    """Non-executable override files should surface an exit code of 126."""
    script = tmp_path / "tool"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o644)

    result = _validate_override_path("tool", os.fspath(script))

    assert isinstance(result, Response)
    assert result.exit_code == 126
    assert "not executable" in result.stderr


def test_validate_override_path_accepts_relative_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Relative paths are resolved against the current working directory."""
    script = tmp_path / "tool"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o755)
    monkeypatch.chdir(tmp_path)

    result = _validate_override_path("tool", "tool")

    assert isinstance(result, Path)
    assert result == script
    assert result.is_absolute()


def test_merge_passthrough_path_filters_shim_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shim directory should be removed when constructing lookup paths."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    socket_path = shim_dir / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, os.fspath(socket_path))

    env_path = os.pathsep.join([os.fspath(shim_dir), "/usr/bin", "/opt/tools"])
    lookup_path = os.pathsep.join(["/custom/bin", "/usr/bin"])

    merged = _merge_passthrough_path(env_path, lookup_path)

    assert merged.split(os.pathsep) == ["/usr/bin", "/opt/tools", "/custom/bin"]


def test_merge_passthrough_path_is_case_insensitive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Duplicate entries differing only in case should collapse on Windows."""
    monkeypatch.setattr(shim.os, "pathsep", ";")
    monkeypatch.setattr("cmd_mox._path_utils.IS_WINDOWS", True)
    shim_dir = tmp_path / "Shim"
    shim_dir.mkdir()
    socket_path = shim_dir / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, os.fspath(socket_path))

    separator = shim.os.pathsep
    env_path = separator.join([os.fspath(shim_dir), r"C:\Tools"])
    lookup_path = separator.join([r"c:\tools", r"C:\Other"])

    merged = shim._merge_passthrough_path(env_path, lookup_path)

    assert merged.split(separator) == [r"C:\Tools", r"C:\Other"]


def test_resolve_passthrough_target_prefers_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Environment overrides should bypass PATH resolution entirely."""
    script = tmp_path / "real"
    script.write_text("#!/bin/sh\necho real\n")
    script.chmod(0o755)

    monkeypatch.setenv(f"{CMOX_REAL_COMMAND_ENV_PREFIX}echo", os.fspath(script))

    directive = PassthroughRequest(
        invocation_id="abc",
        lookup_path="/bin",
        extra_env={},
        timeout=30,
    )
    invocation = Invocation(command="echo", args=[], stdin="", env={})
    env = {"PATH": "/usr/bin"}

    resolved = _resolve_passthrough_target(invocation, directive, env)

    assert resolved == script


def test_resolve_passthrough_target_merges_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PATH resolution should exclude the shim directory and de-duplicate entries."""
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    socket_path = shim_dir / "ipc.sock"
    monkeypatch.setenv(CMOX_IPC_SOCKET_ENV, os.fspath(socket_path))

    invocation = Invocation(command="echo", args=[], stdin="", env={})
    directive = PassthroughRequest(
        invocation_id="abc",
        lookup_path=os.pathsep.join(["/custom/bin", "/usr/bin"]),
        extra_env={},
        timeout=30,
    )
    env = {
        "PATH": _merge_passthrough_path(
            os.pathsep.join([os.fspath(shim_dir), "/usr/bin"]),
            directive.lookup_path,
        )
    }

    captured_path: str | None = None

    def fake_resolve(command: str, path: str, override: str | None = None) -> Path:
        nonlocal captured_path
        captured_path = path
        assert override is None
        return Path("/usr/bin/echo")

    monkeypatch.setattr(shim, "resolve_command_with_override", fake_resolve)

    resolved = _resolve_passthrough_target(invocation, directive, env)

    assert isinstance(resolved, Path)
    assert captured_path == env["PATH"]
