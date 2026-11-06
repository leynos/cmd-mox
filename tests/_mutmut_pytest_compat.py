"""Compatibility support for the pinned mutmut and pytest versions."""

from __future__ import annotations

import sys

import pytest


def _install_mutmut_pytest_item_compatibility() -> None:
    """Restore the legacy item attribute required by mutmut 3.6."""
    if "mutmut" not in sys.modules or hasattr(pytest.Item, "_nodeid"):
        return

    # pytest.Item declares no `_nodeid`, so install the alias at runtime.
    type.__setattr__(pytest.Item, "_nodeid", property(lambda item: item.nodeid))
