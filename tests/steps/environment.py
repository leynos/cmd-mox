"""pytest-bdd steps that manage environment and platform conditions."""

from __future__ import annotations

import os
import typing as t

from pytest_bdd import given, parsers, when

from cmd_mox.environment import CMOX_REAL_COMMAND_ENV_PREFIX

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    import pytest


@given("windows shim launchers are enabled")
def enable_windows_shims(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force shim generation to emit Windows batch launchers."""
    monkeypatch.setattr("cmd_mox.shimgen.IS_WINDOWS", True)


@given(parsers.cfparse('the platform override is "{platform}"'))
def set_platform_override(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    """Simulate running on an alternate platform such as Windows."""
    monkeypatch.setenv("CMD_MOX_PLATFORM_OVERRIDE", platform)


@given(parsers.cfparse('I set environment variable "{var}" to "{val}"'))
@when(parsers.cfparse('I set environment variable "{var}" to "{val}"'))
def set_env_var(monkeypatch: pytest.MonkeyPatch, var: str, val: str) -> None:
    """Adjust environment variable to new value (scoped to the test)."""
    monkeypatch.setenv(var, val)


@given(parsers.cfparse('the command "{cmd}" resolves to a non-executable file'))
def non_executable_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cmd: str,
) -> None:
    """Place a non-executable *cmd* earlier in ``PATH`` for passthrough tests."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    dummy = bin_dir / cmd
    dummy.write_text("#!/bin/sh\necho hi\n")
    dummy.chmod(0o644)

    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{original_path}")
    monkeypatch.setenv(f"{CMOX_REAL_COMMAND_ENV_PREFIX}{cmd}", str(dummy))


@given(parsers.cfparse('the command "{cmd}" will timeout'))
def command_will_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cmd: str,
) -> None:
    """Return a deterministic timeout-like response for *cmd*."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / cmd
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('timeout after 30 seconds\\n')\n"
        "sys.exit(124)\n"
    )
    script.chmod(0o755)

    original_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{original_path}")
    monkeypatch.setenv(f"{CMOX_REAL_COMMAND_ENV_PREFIX}{cmd}", str(script))
