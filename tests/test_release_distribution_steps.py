"""Tests for collecting distributions before release attestation."""

from __future__ import annotations

import typing as typ

import pytest
from helpers.release_workflow import (
    load_workflow_step,
    run_bash_step,
)
from helpers.release_workflow import (
    release_shell_marks as pytestmark,
)

__all__ = ["pytestmark"]

if typ.TYPE_CHECKING:
    from pathlib import Path


def _distribution_tree(root: Path) -> tuple[Path, Path, Path]:
    """Create the input distribution tree used by the workflow shell tests.

    Returns
    -------
    tuple[pathlib.Path, pathlib.Path, pathlib.Path]
        The ``dist`` directory, the downloaded artifact directory, and the
        publication directory.
    """
    dist = root / "dist"
    source = dist / "wheels-pure"
    publish = dist / "publish"
    source.mkdir(parents=True)
    publish.mkdir()
    return dist, source, publish


def test_attestation_collection_copies_all_wheels_and_one_sdist(tmp_path: Path) -> None:
    """The collection step copies all input wheels and exactly one sdist."""
    _, source, publish = _distribution_tree(tmp_path)
    (source / "cmd_mox-0.3.0.tar.gz").write_bytes(b"sdist")
    (source / "cmd_mox-0.3.0-py3-none-any.whl").write_bytes(b"wheel one")
    (source / "cmd_mox-0.3.0-py3-none-any.whl.metadata").write_bytes(b"metadata")
    (source / "cmd_mox-0.3.0-py2.py3-none-any.whl").write_bytes(b"wheel two")
    (publish / "stale.whl").write_bytes(b"must not be copied")
    script, _ = load_workflow_step("attest", "collect")

    result = run_bash_step(
        script,
        cwd=tmp_path,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 0, result.stderr
    assert {path.name for path in publish.iterdir()} == {
        "cmd_mox-0.3.0.tar.gz",
        "cmd_mox-0.3.0-py3-none-any.whl",
        "cmd_mox-0.3.0-py2.py3-none-any.whl",
    }, "collection must include every wheel but not scan its output directory"


@pytest.mark.parametrize(
    "sdist_names",
    [[], ["one.tar.gz", "two.tar.gz"]],
    ids=["missing-sdist", "two-sdists"],
)
def test_attestation_collection_fails_without_exactly_one_sdist(
    tmp_path: Path, sdist_names: list[str]
) -> None:
    """Collection fails before attestation for zero or multiple sdists."""
    _, source, _ = _distribution_tree(tmp_path)
    for name in sdist_names:
        (source / name).write_bytes(b"sdist")
    (source / "cmd_mox-0.3.0-py3-none-any.whl").write_bytes(b"wheel")
    script, _ = load_workflow_step("attest", "collect")

    result = run_bash_step(script, cwd=tmp_path, env={"PATH": "/usr/bin:/bin"})

    assert result.returncode == 1, "invalid source-distribution counts must fail"
    assert "Expected exactly one sdist" in result.stderr, (
        "collection failure must identify the sdist inventory"
    )
