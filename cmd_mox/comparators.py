"""Simple comparator classes used for argument matching."""

from __future__ import annotations

import collections.abc as cabc
import re
import typing as typ


class _ReprMixin:
    """Generate ``repr`` from public attributes.

    Private attributes (those starting with ``_``) are omitted. Attributes are
    sorted alphabetically to keep the output stable even if ``__init__`` order
    changes.
    """

    def __repr__(self) -> str:
        attrs = ", ".join(
            f"{k}={v!r}" for k, v in sorted(vars(self).items()) if not k.startswith("_")
        )
        return f"{self.__class__.__name__}({attrs})"


class Comparator(typ.Protocol):
    """Callable returning ``True`` when a value matches."""

    def __call__(self, value: str) -> bool:
        """Return ``True`` if *value* satisfies the comparison."""
        ...


class Any(_ReprMixin):
    """Match any value."""

    def __call__(self, value: str) -> bool:
        """Return ``True`` for any input.

        Returns
        -------
        bool
            Always ``True``.
        """
        return True


class IsA(_ReprMixin):
    """Match values convertible to ``typ``."""

    def __init__(self, typ: type) -> None:
        self.typ = typ

    def __call__(self, value: str) -> bool:
        """Return ``True`` when ``value`` converts to ``typ``.

        Returns
        -------
        bool
            Whether conversion succeeds.
        """
        try:
            self.typ(value)
        except Exception:  # ruff: ignore[blind-except] - conversion may fail
            return False
        return True


class Regex(_ReprMixin):
    """Match if *value* matches ``pattern``."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self._pattern = re.compile(pattern)

    def __call__(self, value: str) -> bool:
        """Return ``True`` if the regex matches *value*.

        Returns
        -------
        bool
            Whether the configured pattern matches *value*.
        """
        return bool(self._pattern.search(value))


class Contains(_ReprMixin):
    """Match if ``substring`` is found in *value*."""

    def __init__(self, substring: str) -> None:
        self.substring = substring

    def __call__(self, value: str) -> bool:
        """Return ``True`` if ``substring`` is in *value*.

        Returns
        -------
        bool
            Whether *value* contains the configured substring.
        """
        return self.substring in value


class StartsWith(_ReprMixin):
    """Match if *value* begins with ``prefix``."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def __call__(self, value: str) -> bool:
        """Return ``True`` if *value* starts with ``prefix``.

        Returns
        -------
        bool
            Whether *value* starts with the configured prefix.
        """
        return value.startswith(self.prefix)


class Predicate(_ReprMixin):
    """Use a custom ``func`` to determine a match."""

    def __init__(self, func: cabc.Callable[[str], object]) -> None:
        """Create a predicate that evaluates ``func`` and coerces to bool."""
        self.func = func

    def __call__(self, value: str) -> bool:
        """Return ``True`` if ``func(value)`` is truthy.

        Returns
        -------
        bool
            The predicate result coerced to ``bool``.
        """
        return bool(self.func(value))


__all__ = [
    "Any",
    "Comparator",
    "Contains",
    "IsA",
    "Predicate",
    "Regex",
    "StartsWith",
]
