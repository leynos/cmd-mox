"""Unit tests for shim passthrough helpers."""

from __future__ import annotations

import os
import typing as t
from pathlib import Path

import pytest

from cmd_mox.ipc import Response
from cmd_mox.shim import _validate_override_path


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
)
def test_validate_override_path_reports_missing_or_invalid_targets(
    tmp_path: Path,
    factory: t.Callable[[Path], Path],
    expected_exit: int,
    expected_message: str,
) -> None:
    """Validate error handling for nonexistent and non-file overrides."""
    target = factory(tmp_path)
    result = _validate_override_path("tool", os.fspath(target))

    assert isinstance(result, Response)
    assert result.exit_code == expected_exit
    assert expected_message in result.stderr


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
