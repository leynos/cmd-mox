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
    """Invoke ``pytest.skip`` via a typed cast so ``ty`` sees ``NoReturn``.

    Parameters
    ----------
    reason : str
        The skip reason forwarded to ``pytest.skip``.

    Raises
    ------
    pytest.skip.Exception
        Always raised; the function never returns normally.
    """
    skip = t.cast("t.Callable[[str], t.NoReturn]", pytest.skip)
    skip(reason)


def pytest_fail(reason: str) -> t.NoReturn:
    """Invoke ``pytest.fail`` via a typed cast so ``ty`` sees ``NoReturn``.

    Parameters
    ----------
    reason : str
        The failure reason forwarded to ``pytest.fail``.

    Raises
    ------
    pytest.fail.Exception
        Always raised; the function never returns normally.
    """
    fail = t.cast("t.Callable[[str], t.NoReturn]", pytest.fail)
    fail(reason)
