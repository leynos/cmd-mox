"""Simple comparator classes used for argument matching."""

from __future__ import annotations

import re
import typing as t


class _ReprMixin:
    """Provide a generic ``__repr__`` based on public attributes."""

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        attrs = ", ".join(
            f"{k}={v!r}" for k, v in vars(self).items() if not k.startswith("_")
        )
        return f"{self.__class__.__name__}({attrs})"


class Comparator(t.Protocol):
    """Callable returning ``True`` when a value matches."""

    def __call__(self, value: str) -> bool:
        """Return ``True`` if *value* satisfies the comparison."""
        ...


class Any(_ReprMixin):
    """Match any value."""

    def __call__(self, value: str) -> bool:
        """Return ``True`` for any input."""
        return True


class IsA(_ReprMixin):
    """Match values convertible to ``typ``."""

    def __init__(self, typ: type) -> None:
        self.typ = typ

    def __call__(self, value: str) -> bool:
        """Return ``True`` when ``value`` converts to ``typ``."""
        try:
            self.typ(value)
        except Exception:  # noqa: BLE001 - conversion may fail
            return False
        return True


class Regex(_ReprMixin):
    """Match if *value* matches ``pattern``."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self._pattern = re.compile(pattern)

    def __call__(self, value: str) -> bool:
        """Return ``True`` if the regex matches *value*."""
        return bool(self._pattern.search(value))


class Contains(_ReprMixin):
    """Match if ``substring`` is found in *value*."""

    def __init__(self, substring: str) -> None:
        self.substring = substring

    def __call__(self, value: str) -> bool:
        """Return ``True`` if ``substring`` is in *value*."""
        return self.substring in value


class StartsWith(_ReprMixin):
    """Match if *value* begins with ``prefix``."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def __call__(self, value: str) -> bool:
        """Return ``True`` if *value* starts with ``prefix``."""
        return value.startswith(self.prefix)


class Predicate(_ReprMixin):
    """Use a custom ``func`` to determine a match."""

    def __init__(self, func: t.Callable[[str], bool]) -> None:
        self.func = func

    def __call__(self, value: str) -> bool:
        """Return ``True`` if ``func(value)`` is truthy."""
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
