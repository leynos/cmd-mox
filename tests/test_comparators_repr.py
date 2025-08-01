"""Unit tests for :mod:`cmd_mox.comparators` repr behaviour."""

from cmd_mox.comparators import Any, Contains, IsA, Predicate, Regex, StartsWith


def test_any_repr() -> None:
    """``Any`` shows only its class name."""
    assert repr(Any()) == "Any()"


def test_is_a_repr() -> None:
    """``IsA`` includes the ``typ`` parameter."""
    assert repr(IsA(int)) == "IsA(typ=<class 'int'>)"


class CustomType:
    """Example user-defined type for ``IsA`` tests."""

    pass


def test_is_a_repr_with_custom_type() -> None:
    """User-defined classes show their fully-qualified name."""
    expected = f"IsA(typ=<class '{CustomType.__module__}.{CustomType.__qualname__}'>)"
    assert repr(IsA(CustomType)) == expected


def test_regex_repr() -> None:
    """``Regex`` exposes its original pattern string."""
    assert repr(Regex(r"^foo$")) == "Regex(pattern='^foo$')"


def test_contains_repr() -> None:
    """``Contains`` shows the substring."""
    assert repr(Contains("bar")) == "Contains(substring='bar')"


def test_startswith_repr() -> None:
    """``StartsWith`` shows the prefix."""
    assert repr(StartsWith("bar")) == "StartsWith(prefix='bar')"


def test_predicate_repr() -> None:
    """``Predicate`` includes the callable reference."""

    def func(v: str) -> bool:
        return True

    rep = repr(Predicate(func))
    assert rep.startswith("Predicate(func=<function")
    assert rep.endswith(")")


def test_predicate_repr_lambda() -> None:
    """Lambda functions are represented generically."""
    rep = repr(Predicate(lambda v: True))
    assert rep.startswith("Predicate(func=<function")
    assert "lambda" in rep
    assert rep.endswith(")")


def test_predicate_repr_builtin() -> None:
    """Built-in functions include their name."""
    rep = repr(Predicate(str.isdigit))
    assert "isdigit" in rep
    assert rep.startswith("Predicate(func=<")
    assert rep.endswith(")")
