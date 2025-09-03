"""Comparator helpers and expectation matching tests."""

from __future__ import annotations

import re
import typing as t
from types import SimpleNamespace

import pytest

from cmd_mox.comparators import (
    Any as AnyComparator,
)
from cmd_mox.comparators import (
    Contains,
    IsA,
    Predicate,
    Regex,
    StartsWith,
)
from cmd_mox.errors import UnexpectedCommandError
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation
from cmd_mox.verifiers import UnexpectedCommandVerifier


@pytest.mark.parametrize(
    ("matcher", "good", "bad", "expected_repr"),
    [
        (AnyComparator(), "anything", None, "Any()"),
        (IsA(int), "42", "nope", "IsA(typ=<class 'int'>)"),
        (Contains("bar"), "foobarbaz", "qux", "Contains(substring='bar')"),
        (StartsWith("bar"), "barfly", "foobar", "StartsWith(prefix='bar')"),
    ],
)
def test_matchers_match_and_repr(
    matcher: t.Callable[[object], bool],
    good: object,
    bad: object | None,
    expected_repr: str,
) -> None:
    """Matchers evaluate values and provide helpful reprs."""
    assert matcher(good)
    if bad is not None:
        assert not matcher(bad)
    assert repr(matcher) == expected_repr


class CustomType:
    """Example user-defined type for IsA tests."""

    pass


def test_is_a_repr_with_custom_type() -> None:
    """User-defined classes show their fully-qualified name in repr."""
    expected = f"IsA(typ=<class '{CustomType.__module__}.{CustomType.__qualname__}'>)"
    assert repr(IsA(CustomType)) == expected


def test_regex_matches_and_repr() -> None:
    """Regex matches via search and exposes its pattern."""
    pattern = r"^foo\d$"
    matcher = Regex(pattern)
    assert matcher("foo1")
    assert not matcher("bar")
    assert repr(matcher) == f"Regex(pattern={pattern!r})"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("", False),
        (123, True),
        (123.0, True),
        ([], False),
        ({}, False),
    ],
)
def test_is_a_edge_cases(value: object, *, expected: bool) -> None:
    """IsA handles unexpected value types gracefully."""
    matcher = IsA(int)
    assert matcher(value) is expected  # type: ignore[arg-type]


def test_regex_invalid_pattern_raises() -> None:
    """Regex raises an error when the pattern is malformed."""
    with pytest.raises(re.error):
        Regex("[unclosed")


@pytest.mark.parametrize("value", [123, None, ["foo1"]])
def test_regex_non_string_input(value: object) -> None:
    """Regex matcher raises TypeError for non-string values."""
    matcher = Regex(r"^foo\d$")
    with pytest.raises(TypeError):
        matcher(value)  # type: ignore[arg-type]


def test_predicate_matches_and_repr() -> None:
    """Predicate delegates to the provided function."""
    matcher = Predicate(str.isupper)
    assert matcher("HELLO")
    assert not matcher("hi")
    rep = repr(matcher)
    assert rep.startswith("Predicate(func=<")
    assert rep.endswith(")")


def test_predicate_raises_exception() -> None:
    """Predicate propagates exceptions from the wrapped function."""

    def raises_exc(_: str) -> bool:
        msg = "Test exception"
        raise ValueError(msg)

    matcher = Predicate(raises_exc)
    with pytest.raises(ValueError, match="Test exception"):
        matcher("anything")


def test_predicate_non_boolean_return() -> None:
    """Predicate coerces the function result to bool."""
    matcher_true = Predicate(lambda _: "not a bool")
    assert matcher_true("anything")
    matcher_false = Predicate(lambda _: "")
    assert not matcher_false("anything")


def test_expectation_with_matchers() -> None:
    """Expectation uses comparator objects for flexible argument matching."""
    exp = Expectation("cmd").with_matching_args(
        AnyComparator(),
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


@pytest.mark.parametrize(
    "args",
    [
        ["oops", "foo"],  # first matcher fails
        ["123", "bar"],  # second matcher fails
        ["123"],  # argument count mismatch
    ],
)
def test_expectation_with_matchers_failure(args: list[str]) -> None:
    """Expectation fails when arguments do not satisfy matchers."""
    exp = Expectation("cmd").with_matching_args(IsA(int), Contains("foo"))
    inv = Invocation(command="cmd", args=args, stdin="", env={})
    assert not exp.matches(inv)


def test_expectation_with_matchers_failure_message() -> None:
    """Failure message identifies which comparator rejected the argument."""
    exp = Expectation("cmd").with_matching_args(IsA(int))
    inv = Invocation(command="cmd", args=["oops"], stdin="", env={})
    verifier = UnexpectedCommandVerifier()
    dbl = SimpleNamespace(expectation=exp, kind="mock")
    with pytest.raises(UnexpectedCommandError) as excinfo:
        verifier.verify([inv], {"cmd": dbl})
    assert "arg[0]='oops' failed IsA(typ=<class 'int'>)" in str(excinfo.value)
