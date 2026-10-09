"""Tests for shim bootstrap and import-path setup."""

from __future__ import annotations

import sys
import typing as typ

import pytest

from cmd_mox import shim
from cmd_mox.ipc import Invocation, Response

if typ.TYPE_CHECKING:
    from pathlib import Path


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

    invocation = Invocation(command="shim", args=[], stdin="", env={})
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
        shim, "_write_response", lambda resp: calls.append(("write", resp))
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
        ("write", response),
    ]


def test_bootstrap_shim_path_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling bootstrap_shim_path repeatedly should be safe and stable."""
    from cmd_mox import _shim_bootstrap

    monkeypatch.setattr(_shim_bootstrap, "_BOOTSTRAP_DONE", False)
    monkeypatch.setattr(sys, "path", ["__editable__dummy", "/usr/lib/python3.12"])

    _shim_bootstrap.bootstrap_shim_path()
    path_after_first = list(sys.path)

    _shim_bootstrap.bootstrap_shim_path()

    assert sys.path == path_after_first
    assert sys.modules["platform"].__name__ == "platform"


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
    assert fake_platform.MARKER == "fake"

    _shim_bootstrap.bootstrap_shim_path()

    std_platform = sys.modules["platform"]
    assert not hasattr(std_platform, "MARKER")
    assert "__editable__site" in sys.path


def test_bootstrap_shim_path_restores_sys_path_when_platform_load_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrapping should restore removed entries when platform loading fails."""
    from cmd_mox import _shim_bootstrap

    original_path = ["__editable__dummy", "/usr/lib/python3.12"]
    monkeypatch.setattr(_shim_bootstrap, "_BOOTSTRAP_DONE", False)
    monkeypatch.setattr(sys, "path", list(original_path))

    def raise_runtime_error() -> typ.NoReturn:
        msg = "platform load failed"
        raise RuntimeError(msg)

    monkeypatch.setattr(_shim_bootstrap, "_load_stdlib_platform", raise_runtime_error)

    with pytest.raises(RuntimeError, match="platform load failed"):
        _shim_bootstrap.bootstrap_shim_path()

    assert sys.path == original_path
    assert not _shim_bootstrap._BOOTSTRAP_DONE
