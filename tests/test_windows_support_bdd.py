"""Behavioural tests covering Windows shims and passthrough spies."""

from __future__ import annotations

import logging
import os
import typing as typ
from pathlib import Path

import pytest
from pytest_bdd import scenario

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows-only behavioural tests"
)

FEATURES_DIR = Path(__file__).resolve().parent.parent / "features"

from tests.steps import *  # ruff: ignore[undefined-local-with-import-star, module-import-not-at-top-of-file] - import shared step definitions

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@pytest.fixture(autouse=True)
def announce_windows_smoke() -> cabc.Iterator[None]:
    """Emit log breadcrumbs that end up in the Windows IPC artefact."""
    logger = logging.getLogger("cmd_mox.ipc.smoke")
    logger.info("Starting Windows smoke scenario")
    yield
    logger.info("Completed Windows smoke scenario")


@scenario(
    str(FEATURES_DIR / "windows_support.feature"),
    "Windows shims support mocks and passthrough spies",
)
def test_windows_shim_smoke() -> None:
    """Exercise mocked commands and passthrough spies via Windows shims."""
    pass


@scenario(
    str(FEATURES_DIR / "windows_support.feature"),
    "Windows shims preserve arguments with spaces and carets",
)
def test_windows_argument_preservation() -> None:
    """Ensure Windows launchers forward tricky arguments untouched."""
    pass
