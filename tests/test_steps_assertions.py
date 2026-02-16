"""Unit tests for helper assertions used by pytest-bdd steps."""

from __future__ import annotations

import typing as t

import pytest

from cmd_mox.controller import CmdMox
from tests.steps.assertions import check_shim_suffix

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path


def test_check_shim_suffix_mismatch(tmp_path: Path) -> None:
    """The shim suffix assertion should fail when the suffix differs."""
    shim = tmp_path / "example.cmd"
    shim.touch()

    mox = CmdMox()
    mox.environment.shim_dir = tmp_path
    with pytest.raises(AssertionError):
        check_shim_suffix(mox, "example", ".bat")
