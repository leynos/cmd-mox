"""Compatibility checks for the pinned mutation runner and pytest.

Mutmut 3.6 reads ``pytest.Item._nodeid``, an alias pytest 9 removed.
``_install_mutmut_pytest_item_compatibility`` restores it as a read-only
property over ``nodeid``, but only while ``mutmut`` is imported and the alias
is absent. These tests cover three conditions: the helper adds the alias when
a mutation run has loaded mutmut, the helper leaves pytest untouched without
mutmut, and the root ``conftest.py`` calls the helper during
``pytest_configure`` so a real collected item exposes ``_nodeid``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests._mutmut_pytest_compat import _install_mutmut_pytest_item_compatibility

ROOT = Path(__file__).resolve().parents[1]


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


def test_pytest_configure_installs_the_alias_for_a_real_item(
    tmp_path: Path,
) -> None:
    """The start-up hook, not just the helper, must reach a collected item.

    The nested run mirrors mutmut's own invocation: mutmut is already imported
    when pytest starts, and the root ``conftest.py`` is loaded as a plugin so
    its ``pytest_configure`` runs. Removing that call makes the probe fail with
    ``AttributeError``, which is the baseline failure this compatibility layer
    exists to prevent.
    """
    probe = tmp_path / "test_probe.py"
    probe.write_text(
        "def test_legacy_nodeid_matches_the_public_nodeid(request):\n"
        "    assert request.node._nodeid == request.node.nodeid\n",
        encoding="utf-8",
    )
    script = (
        "import sys, types\n"
        "sys.modules['mutmut'] = types.ModuleType('mutmut')\n"
        "import pytest\n"
        "raise SystemExit(pytest.main([\n"
        "    '-p', 'conftest',\n"
        "    '--rootdir', sys.argv[1],\n"
        "    '-q', '--no-header', '-p', 'no:cacheprovider',\n"
        "    sys.argv[2],\n"
        "]))\n"
    )
    environment = {**os.environ, "PYTEST_ADDOPTS": ""}

    result = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] - fixed argv
        [sys.executable, "-c", script, str(tmp_path), str(probe)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=environment,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout, result.stdout
