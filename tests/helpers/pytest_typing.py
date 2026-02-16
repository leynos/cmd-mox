"""Typed wrappers for ``pytest.skip`` and ``pytest.fail`` for ``ty`` compatibility.

The ``ty`` type checker does not recognise ``pytest.skip`` and
``pytest.fail`` as returning ``NoReturn``, which causes false positives
in unreachable-code analysis.  These wrappers provide the correct
signature via ``typing.cast``.
"""

from __future__ import annotations

import typing as t

import pytest


def pytest_skip(reason: str) -> t.NoReturn:
    """Invoke ``pytest.skip`` through a typed callable cast for ``ty``."""
    skip = t.cast("t.Callable[[str], t.NoReturn]", pytest.skip)
    skip(reason)


def pytest_fail(reason: str) -> t.NoReturn:
    """Invoke ``pytest.fail`` through a typed callable cast for ``ty``."""
    fail = t.cast("t.Callable[[str], t.NoReturn]", pytest.fail)
    fail(reason)
