"""Unit tests for helper assertions used by pytest-bdd steps."""

from __future__ import annotations

import types
import typing as t

import pytest

from tests.steps.assertions import check_shim_suffix

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from cmd_mox.controller import CmdMox


def test_check_shim_suffix_mismatch(tmp_path: Path) -> None:
    """The shim suffix assertion should fail when the suffix differs."""

    class DummyMox:
        def __init__(self, directory: Path) -> None:
            self.environment = types.SimpleNamespace(shim_dir=str(directory))

    shim = tmp_path / "example.cmd"
    shim.touch()

    mox = DummyMox(tmp_path)
    with pytest.raises(AssertionError):
        check_shim_suffix(t.cast("CmdMox", mox), "example", ".bat")
