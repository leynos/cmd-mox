"""Compatibility support for the pinned mutmut and pytest versions.

Mutmut 3.6 reads ``pytest.Item._nodeid`` while attributing test durations,
but pytest 9 removed that private alias in favour of the public ``nodeid``.
A mutation run therefore fails its baseline with an ``AttributeError`` the
moment pytest reports a test, even though the suite is healthy.

:func:`_install_mutmut_pytest_item_compatibility` installs ``_nodeid`` as a
read-only property over ``nodeid``. It acts only when ``mutmut`` is already
imported (a mutation run) and when the alias is absent, so ordinary test
runs and future pytest releases that restore the alias are untouched. The
root ``conftest.py`` calls it during start-up, which is early enough for
every item mutmut observes.
"""

from __future__ import annotations

import sys

import pytest


def _install_mutmut_pytest_item_compatibility() -> None:
    """Restore the legacy item attribute required by mutmut 3.6."""
    if "mutmut" not in sys.modules or hasattr(pytest.Item, "_nodeid"):
        return

    # pytest.Item declares no `_nodeid`, so install the alias at runtime.
    type.__setattr__(pytest.Item, "_nodeid", property(lambda item: item.nodeid))
