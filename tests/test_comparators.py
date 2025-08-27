"""Tests for comparator helpers and expectation matcher plumbing."""

from __future__ import annotations

from cmd_mox.comparators import Any, Contains, IsA, Predicate, Regex, StartsWith
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation


def test_any_matches_and_repr() -> None:
    """Any matches all values and has a simple repr."""
    matcher = Any()
    assert matcher("anything")
    assert repr(matcher) == "Any()"


def test_is_a_matches_and_repr() -> None:
    """IsA converts the value to the given type for matching."""
    matcher = IsA(int)
    assert matcher("42")
    assert not matcher("nope")
    assert repr(matcher) == "IsA(typ=<class 'int'>)"


class CustomType:
    """Example user-defined type for IsA tests."""

    pass


def test_is_a_repr_with_custom_type() -> None:
    """User-defined classes show their fully-qualified name."""
    expected = f"IsA(typ=<class '{CustomType.__module__}.{CustomType.__qualname__}'>)"
    assert repr(IsA(CustomType)) == expected


def test_regex_matches_and_repr() -> None:
    """Regex matches via search and exposes its pattern."""
    pattern = "^foo\\d$"
    matcher = Regex(pattern)
    assert matcher("foo1")
    assert not matcher("bar")
    assert repr(matcher) == f"Regex(pattern={pattern!r})"


def test_contains_matches_and_repr() -> None:
    """Contains checks for substring membership."""
    matcher = Contains("bar")
    assert matcher("foobarbaz")
    assert not matcher("qux")
    assert repr(matcher) == "Contains(substring='bar')"


def test_startswith_matches_and_repr() -> None:
    """StartsWith verifies prefix matches."""
    matcher = StartsWith("bar")
    assert matcher("barfly")
    assert not matcher("foobar")
    assert repr(matcher) == "StartsWith(prefix='bar')"


def test_predicate_matches_and_repr() -> None:
    """Predicate delegates to the provided function."""
    matcher = Predicate(str.isupper)
    assert matcher("HELLO")
    assert not matcher("hi")
    rep = repr(matcher)
    assert rep.startswith("Predicate(func=<")
    assert rep.endswith(")")


def test_expectation_with_matchers() -> None:
    """Expectation uses comparator objects for flexible argument matching."""
    exp = Expectation("cmd").with_matching_args(
        Any(),
        IsA(int),
        Regex(r"^foo\d+$"),
        Contains("bar"),
        StartsWith("baz"),
        Predicate(str.isupper),
    )
    inv = Invocation(
        command="cmd",
        args=["anything", "123", "foo7", "zzbarzz", "bazooka", "HELLO"],
        stdin="",
        env={},
    )
    assert exp.matches(inv)


def test_expectation_with_matchers_failure() -> None:
    """Expectation fails when arguments do not satisfy matchers."""
    exp = Expectation("cmd").with_matching_args(IsA(int))
    inv = Invocation(command="cmd", args=["oops"], stdin="", env={})
    assert not exp.matches(inv)
