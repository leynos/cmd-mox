"""Example tests demonstrating shell pipelines with CmdMox."""

from __future__ import annotations

import os
import subprocess
import typing as t

import pytest

pytest_plugins = ("cmd_mox.pytest_plugin",)

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from cmd_mox.controller import CmdMox

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="Pipeline example assumes a POSIX shell"
)


def test_pipeline_composes_multiple_mocks(cmd_mox: CmdMox) -> None:
    """Mocks can be combined to test shell pipelines."""
    cmd_mox.mock("grep").with_args("foo", "file.txt").returns(stdout="c a b")
    cmd_mox.mock("sort").with_args("-r").with_stdin("c a b").returns(stdout="c b a")

    result = subprocess.run(  # noqa: S602
        "grep foo file.txt | sort -r",  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
        shell=True,
    )

    assert result.stdout.strip() == "c b a"
