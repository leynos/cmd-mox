"""Compatibility checks for the pinned mutation runner and pytest."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from tests._mutmut_pytest_compat import _install_mutmut_pytest_item_compatibility


def test_mutmut_legacy_nodeid_is_available_when_mutmut_is_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutmut 3.6 expects the private item alias removed by pytest 9."""
    with monkeypatch.context() as scoped_monkeypatch:
        scoped_monkeypatch.setitem(sys.modules, "mutmut", ModuleType("mutmut"))
        scoped_monkeypatch.delattr(pytest.Item, "_nodeid", raising=False)

        _install_mutmut_pytest_item_compatibility()

        # The alias exists only at runtime, so read it from the class mapping.
        legacy_nodeid = vars(pytest.Item)["_nodeid"]
        assert isinstance(legacy_nodeid, property), legacy_nodeid
        example_item = type("ExampleItem", (), {"nodeid": "tests/example.py::test"})()
        assert legacy_nodeid.fget is not None, legacy_nodeid
        assert legacy_nodeid.fget(example_item) == example_item.nodeid, (
            legacy_nodeid.fget(example_item)
        )


def test_mutmut_legacy_nodeid_is_not_added_without_mutmut(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the pytest compatibility alias scoped to mutation runs."""
    with monkeypatch.context() as scoped_monkeypatch:
        scoped_monkeypatch.delitem(sys.modules, "mutmut", raising=False)
        scoped_monkeypatch.delattr(pytest.Item, "_nodeid", raising=False)

        _install_mutmut_pytest_item_compatibility()

        assert "_nodeid" not in vars(pytest.Item), (
            "the compatibility shim must not touch pytest without mutmut loaded"
        )
