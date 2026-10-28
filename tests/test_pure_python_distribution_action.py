"""Behavioural checks for the pure-Python sdist and wheel action.

Run these checks with ``make test`` or
``python -m pytest -q tests/test_pure_python_distribution_action.py``. They
execute the checked-in Bash step with a temporary fake ``uv`` command.
"""

from __future__ import annotations

import typing as typ

import pytest
import yaml
from helpers.release_workflow import (
    REPOSITORY_ROOT,
    load_action_build_step,
    run_bash_step,
    write_executable,
)
from helpers.release_workflow import (
    release_shell_marks as pytestmark,
)

__all__ = ["pytestmark"]

if typ.TYPE_CHECKING:
    import subprocess
    from pathlib import Path

_FAKE_UV = """#!/bin/sh
set -eu
test "$#" -eq 3
test "$1" = build
test "$2" = --out-dir
mkdir -p "$3"
if [ "$FAKE_SDIST" = 1 ]; then
  : > "$3/cmd_mox-0.2.0.tar.gz"
fi
if [ "$FAKE_EXTRA_SDIST" = 1 ]; then
  : > "$3/cmd_mox-extra-0.2.0.tar.gz"
fi
: > "$3/$FAKE_WHEEL"
"""
_PURE_WHEEL_NAME = "cmd_mox-0.2.0-py3-none-any.whl"


def _run_build_step(
    tmp_path: Path,
    *,
    has_sdist: bool,
    additional_sdist: bool = False,
    wheel_name: str = _PURE_WHEEL_NAME,
    out_dir: str = "dist",
) -> tuple[subprocess.CompletedProcess[str], Path, Path, Path]:
    """Run the build script with a recording uv stub."""  # ruff: ignore[docstring-missing-returns] - the private helper's tuple is explicit in its annotation.
    script, _ = load_action_build_step()
    output_dir = tmp_path / "dist"
    output_dir.mkdir()
    stale_sdist = output_dir / "cmd_mox-0.1.0.tar.gz"
    stale_sdist.touch()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    write_executable(fake_bin / "uv", _FAKE_UV)
    github_output = tmp_path / "github-output"

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={
            "FAKE_EXTRA_SDIST": "1" if additional_sdist else "0",
            "FAKE_SDIST": "1" if has_sdist else "0",
            "FAKE_WHEEL": wheel_name,
            "GITHUB_OUTPUT": str(github_output),
            "OUT_DIR": out_dir,
            "PATH": f"{fake_bin}:/usr/bin:/bin",
        },
    )
    return result, output_dir, github_output, stale_sdist


def test_build_action_uses_only_the_current_sdist_and_wheel(tmp_path: Path) -> None:
    """Old files in dist cannot enter the new build inventory or upload."""
    result, output_dir, github_output, stale_sdist = _run_build_step(
        tmp_path, has_sdist=True
    )

    assert result.returncode == 0, result.stderr
    assert stale_sdist.exists(), "the pre-existing sdist fixture must remain"
    output_line = github_output.read_text(encoding="utf-8").strip()
    assert output_line.startswith("build-dir=dist/release-build."), (
        "the action must report its isolated build directory"
    )
    build_dir = tmp_path / output_line.removeprefix("build-dir=")
    assert {path.name for path in build_dir.iterdir()} == {
        "cmd_mox-0.2.0.tar.gz",
        "cmd_mox-0.2.0-py3-none-any.whl",
    }, "the upload directory must contain only this build's sdist and wheel"
    assert len(list(output_dir.glob("*.tar.gz"))) == 1, (
        "the stale sdist must remain outside the isolated upload directory"
    )


def test_build_action_fails_when_current_build_has_no_sdist(tmp_path: Path) -> None:
    """A stale sdist cannot hide a missing sdist from this build."""
    result, _, github_output, _ = _run_build_step(tmp_path, has_sdist=False)

    assert result.returncode == 1, "a build without an sdist must fail"
    assert "Expected exactly one sdist" in result.stderr, (
        "the failure must identify the invalid sdist inventory"
    )
    assert not github_output.exists(), "a failed build must not report upload files"


def test_build_action_fails_when_current_build_has_two_sdists(tmp_path: Path) -> None:
    """Multiple source archives cannot pass the exact inventory check."""
    result, _, github_output, _ = _run_build_step(
        tmp_path, has_sdist=True, additional_sdist=True
    )

    assert result.returncode == 1, "a build with two sdists must fail"
    assert "Expected exactly one sdist and at least one wheel" in result.stderr, (
        "the failure must explain the exact accepted inventory"
    )
    assert "found 2 sdist(s) and 1 wheel(s)" in result.stderr, (
        "the failure must report the observed inventory counts"
    )
    assert not github_output.exists(), "a failed build must not report upload files"


def test_build_action_rejects_platform_specific_wheels(tmp_path: Path) -> None:
    """The pure-Python action rejects a platform wheel before upload."""
    result, _, github_output, _ = _run_build_step(
        tmp_path,
        has_sdist=True,
        wheel_name="cmd_mox-0.2.0-cp312-cp312-manylinux_x86_64.whl",
    )

    assert result.returncode == 1, "platform-specific wheels must fail the build"
    assert "must use the py3-none-any tag" in result.stderr, (
        "the failure must explain the accepted wheel tag"
    )
    assert not github_output.exists(), "a rejected wheel must not be reported"


@pytest.mark.parametrize("out_dir", ["", "/"], ids=["empty", "root"])
def test_build_action_rejects_an_empty_or_root_output_directory(
    tmp_path: Path, out_dir: str
) -> None:
    """A build cannot direct temporary output to an empty or root path."""
    result, _, github_output, _ = _run_build_step(
        tmp_path, has_sdist=True, out_dir=out_dir
    )

    assert result.returncode == 1, "invalid output directories must fail before build"
    assert "output directory must be a non-root path" in result.stderr, (
        "the guard must explain the rejected output directory"
    )
    assert not github_output.exists(), "a rejected output directory must not upload"


def test_upload_artifact_includes_both_current_build_formats() -> None:
    """The uploaded artifact covers the isolated sdist and wheel inventory."""
    action_path = (
        REPOSITORY_ROOT / ".github" / "actions" / "pure-python-wheel" / "action.yml"
    )
    action = yaml.safe_load(action_path.read_text(encoding="utf-8"))
    upload = next(
        step
        for step in action["runs"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    config = upload["with"]
    paths = config["path"].splitlines()

    assert paths == [
        "${{ steps.build.outputs.build-dir }}/*.whl",
        "${{ steps.build.outputs.build-dir }}/*.tar.gz",
    ], "the uploaded artifact must include only current-build wheels and sdist"
    assert config["if-no-files-found"] == "error", (
        "an empty artifact inventory must fail the action"
    )
