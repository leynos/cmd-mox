"""Comparator helpers and expectation matching tests."""

from __future__ import annotations

import re
import typing as typ
from types import SimpleNamespace

import pytest
from hypothesis import given
from hypothesis import strategies as st

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
from cmd_mox.test_doubles import CommandDouble, DoubleKind
from cmd_mox.verifiers import UnexpectedCommandVerifier

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from cmd_mox.expectations import ArgMatcher


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
    matcher: cabc.Callable[[typ.Any], bool],
    good: typ.Any,  # ruff: ignore[any-type] - test cases deliberately accept values of multiple runtime types
    bad: typ.Any,  # ruff: ignore[any-type] - test cases deliberately accept values of multiple runtime types
    expected_repr: str,
) -> None:
    """Matchers evaluate values and provide helpful reprs."""
    assert matcher(good), "Assertion failed"
    if bad is not None:
        assert not matcher(bad), "Assertion failed"
    assert repr(matcher) == expected_repr, "Assertion failed"


class CustomType:
    """Example user-defined type for IsA tests."""

    pass


def test_is_a_repr_with_custom_type() -> None:
    """User-defined classes show their fully-qualified name in repr."""
    expected = f"IsA(typ=<class '{CustomType.__module__}.{CustomType.__qualname__}'>)"
    assert repr(IsA(CustomType)) == expected, "Assertion failed"


def _mock_double(expectation: Expectation) -> CommandDouble:
    """Return a typed mock CommandDouble stub exposing the provided expectation.

    Returns
    -------
    CommandDouble
        A stub double of kind ``MOCK`` carrying *expectation*.
    """
    return typ.cast(
        "CommandDouble",
        SimpleNamespace(
            expectation=expectation,
            kind=DoubleKind.MOCK,
            name=expectation.name,
        ),
    )


def test_regex_matches_and_repr() -> None:
    """Regex matches via search and exposes its pattern."""
    pattern = r"^foo\d$"
    matcher = Regex(pattern)
    assert matcher("foo1"), "Assertion failed"
    assert not matcher("bar"), "Assertion failed"
    assert repr(matcher) == f"Regex(pattern={pattern!r})", "Assertion failed"


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
    assert matcher(value) is expected, "Assertion failed"  # type: ignore[arg-type, ty:invalid-argument-type]


def test_regex_invalid_pattern_raises() -> None:
    """Regex raises an error when the pattern is malformed."""
    with pytest.raises(re.error):
        Regex("[unclosed")


@pytest.mark.parametrize("value", [123, None, ["foo1"]])
def test_regex_non_string_input(value: object) -> None:
    """Regex matcher raises TypeError for non-string values."""
    matcher = Regex(r"^foo\d$")
    with pytest.raises(TypeError):
        matcher(value)  # type: ignore[arg-type, ty:invalid-argument-type]


def test_predicate_matches_and_repr() -> None:
    """Predicate delegates to the provided function."""
    matcher = Predicate(str.isupper)
    assert matcher("HELLO"), "Assertion failed"
    assert not matcher("hi"), "Assertion failed"
    rep = repr(matcher)
    assert rep.startswith("Predicate(func=<"), "Assertion failed"
    assert rep.endswith(")"), "Assertion failed"


def test_predicate_raises_exception() -> None:
    """Predicate propagates exceptions from the wrapped function."""

    def raises_exc(_: str) -> bool:
        """Raise ``ValueError`` so the predicate's exception propagates."""
        msg = "Test exception"
        raise ValueError(msg)

    matcher = Predicate(raises_exc)
    with pytest.raises(ValueError, match="Test exception"):
        matcher("anything")


def test_predicate_non_boolean_return() -> None:
    """Predicate coerces the function result to bool."""
    matcher_true = Predicate(lambda _: "not a bool")
    assert matcher_true("anything"), "Assertion failed"
    matcher_false = Predicate(lambda _: "")
    assert not matcher_false("anything"), "Assertion failed"


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
    assert exp.matches(inv), "Assertion failed"


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
    assert not exp.matches(inv), "Assertion failed"


def test_expectation_with_matchers_failure_message() -> None:
    """Failure message identifies which comparator rejected the argument."""
    exp = Expectation("cmd").with_matching_args(IsA(int))
    inv = Invocation(command="cmd", args=["oops"], stdin="", env={})
    verifier = UnexpectedCommandVerifier()
    dbl = _mock_double(exp)
    with pytest.raises(UnexpectedCommandError) as excinfo:
        verifier.verify([inv], {"cmd": dbl})
    assert "arg[0]='oops' failed IsA(typ=<class 'int'>)" in str(excinfo.value), (
        "Assertion failed"
    )


def test_expectation_matcher_exception_returns_false() -> None:
    """Expectation treats comparator exceptions as mismatches."""

    def boom(_: str) -> bool:
        """Raise ``ValueError`` to simulate a failing comparator."""
        raise ValueError("boom")

    exp = Expectation("cmd").with_matching_args(Predicate(boom))
    inv = Invocation(command="cmd", args=["x"], stdin="", env={})
    assert not exp.matches(inv), "Assertion failed"


def test_expectation_matcher_exception_message() -> None:
    """Failure reason reports matcher exceptions with context."""

    def boom(_: str) -> bool:
        """Raise ``ValueError`` to simulate a failing matcher."""
        raise ValueError("boom")

    exp = Expectation("cmd").with_matching_args(Predicate(boom))
    inv = Invocation(command="cmd", args=["x"], stdin="", env={})
    verifier = UnexpectedCommandVerifier()
    dbl = _mock_double(exp)
    with pytest.raises(UnexpectedCommandError) as excinfo:
        verifier.verify([inv], {"cmd": dbl})
    msg = str(excinfo.value)
    assert "arg[0] predicate" in msg, "Assertion failed"
    assert "ValueError: boom" in msg, "Assertion failed"


def test_expectation_with_literal_and_comparator_matchers() -> None:
    """A literal string matches exactly while comparators stay flexible."""
    exp = Expectation("git").with_matching_args("clone", AnyComparator())
    inv = Invocation(
        command="git",
        args=["clone", "https://example.com/repo.git"],
        stdin="",
        env={},
    )
    assert exp.matches(inv), "Assertion failed"


def test_expectation_with_literal_only_matchers() -> None:
    """Literal strings require exact argument equality."""
    exp = Expectation("git").with_matching_args("clone", "url")
    matching = Invocation(command="git", args=["clone", "url"], stdin="", env={})
    differing = Invocation(command="git", args=["clone", "other"], stdin="", env={})
    assert exp.matches(matching), "identical literals should match"
    assert not exp.matches(differing), "different literals must not match"


@pytest.mark.parametrize("argument", ["clon", "clone-extra", "lone", "c"])
def test_expectation_literal_matcher_rejects_partial_matches(
    argument: str,
) -> None:
    """Literal matchers must not accept prefixes, suffixes, or substrings."""
    exp = Expectation("git").with_matching_args("clone")
    inv = Invocation(command="git", args=[argument], stdin="", env={})
    assert not exp.matches(inv), "literal matching must be exact equality"


def test_expectation_mixed_matchers_reject_partial_literal() -> None:
    """A literal inside a mixed matcher list still requires exact equality."""
    exp = Expectation("git").with_matching_args("clone", AnyComparator())
    inv = Invocation(
        command="git",
        args=["clone-extra", "https://example.com/repo.git"],
        stdin="",
        env={},
    )
    assert exp.explain_mismatch(inv) == "arg[0]='clone-extra' != 'clone'", (
        "the first literal mismatch should be reported"
    )


@st.composite
def _literal_and_callable_matchers(draw: st.DrawFn) -> list[ArgMatcher]:
    """Generate literal strings and deterministic equality predicates."""
    matchers: list[ArgMatcher] = []
    for _ in range(draw(st.integers(min_value=0, max_value=4))):
        literal = draw(st.text())
        if draw(st.booleans()):
            matchers.append(literal)
        else:

            def accepts(value: str, expected: str = literal) -> bool:
                """Accept only the generated literal."""
                return value == expected

            matchers.append(accepts)
    return matchers


@given(
    matchers=_literal_and_callable_matchers(),
    args=st.lists(st.text(), max_size=5),
)
def test_expectation_mixed_matchers_report_first_failing_position(
    matchers: list[ArgMatcher],
    args: list[str],
) -> None:
    """Matching requires equal arity and acceptance at every position."""
    exp = Expectation("cmd").with_matching_args(*matchers)
    inv = Invocation(command="cmd", args=args, stdin="", env={})

    if len(args) != len(matchers):
        assert not exp.matches(inv), "an arity mismatch must not match"
        assert exp.explain_mismatch(inv) == (
            f"expected {len(matchers)} args but got {len(args)}"
        ), "arity mismatch should be reported before any position"
        return

    def accepts(matcher: ArgMatcher, value: str) -> bool:
        """Return whether *matcher* accepts *value*."""
        if isinstance(matcher, str):
            return matcher == value
        return bool(matcher(value))

    first_failure = next(
        (
            index
            for index, (matcher, arg) in enumerate(zip(matchers, args, strict=True))
            if not accepts(matcher, arg)
        ),
        None,
    )
    assert exp.matches(inv) is (first_failure is None), (
        "matching must agree with per-position acceptance"
    )
    if first_failure is not None:
        assert exp.explain_mismatch(inv).startswith(f"arg[{first_failure}]"), (
            "the first failing position should be reported"
        )


def test_expectation_literal_matcher_arity_takes_precedence() -> None:
    """Arity is reported before any literal comparison."""
    exp = Expectation("git").with_matching_args("clone", "url")
    inv = Invocation(command="git", args=["clone"], stdin="", env={})
    assert exp.explain_mismatch(inv) == "expected 2 args but got 1", (
        "arity mismatch should be reported first"
    )


def test_expectation_literal_matcher_mismatch_message() -> None:
    """A literal mismatch explains equality rather than calling the literal."""
    exp = Expectation("git").with_matching_args("clone", AnyComparator())
    inv = Invocation(command="git", args=["pull", "url"], stdin="", env={})
    verifier = UnexpectedCommandVerifier()
    dbl = _mock_double(exp)
    with pytest.raises(UnexpectedCommandError) as excinfo:
        verifier.verify([inv], {"git": dbl})
    msg = str(excinfo.value)
    assert "arg[0]='pull' != 'clone'" in msg, "the literal mismatch should be shown"
    assert "TypeError" not in msg, "literal matching must not raise TypeError"
    assert "not callable" not in msg, "a literal must not be treated as callable"
    assert "git('clone', Any())" in msg, "the expected call should render matchers"


def test_expectation_mixed_matcher_exception_message() -> None:
    """A callable exception is still reported after a literal match."""

    def boom(_: str) -> bool:
        """Raise ``ValueError`` to simulate a failing matcher."""
        raise ValueError("boom")

    exp = Expectation("git").with_matching_args("clone", Predicate(boom))
    inv = Invocation(command="git", args=["clone", "x"], stdin="", env={})
    verifier = UnexpectedCommandVerifier()
    dbl = _mock_double(exp)
    with pytest.raises(UnexpectedCommandError) as excinfo:
        verifier.verify([inv], {"git": dbl})
    msg = str(excinfo.value)
    assert "arg[1] predicate" in msg, "the raising matcher should be identified"
    assert "ValueError: boom" in msg, "the matcher exception should be reported"


def test_expectation_stdin_predicate_failure_message() -> None:
    """Reports a helpful reason when stdin predicate fails."""
    exp = Expectation("cmd").with_stdin(lambda s: s == "ok")
    inv = Invocation(command="cmd", args=[], stdin="nope", env={})
    verifier = UnexpectedCommandVerifier()
    dbl = _mock_double(exp)
    with pytest.raises(UnexpectedCommandError) as excinfo:
        verifier.verify([inv], {"cmd": dbl})
    assert "stdin 'nope' failed" in str(excinfo.value), "Assertion failed"


def test_expectation_env_mismatch_message() -> None:
    """Reports missing or mismatched env values."""
    exp = Expectation("cmd").with_env({"FOO": "bar"})
    inv = Invocation(command="cmd", args=[], stdin="", env={"FOO": "baz"})
    verifier = UnexpectedCommandVerifier()
    dbl = _mock_double(exp)
    with pytest.raises(UnexpectedCommandError) as excinfo:
        verifier.verify([inv], {"cmd": dbl})
    assert "env['FOO']='baz' != 'bar'" in str(excinfo.value), "Assertion failed"


def test_expectation_command_mismatch_message() -> None:
    """Explain mismatch when command names differ."""
    exp = Expectation("cmd")
    inv = Invocation(command="other", args=[], stdin="", env={})
    assert exp.explain_mismatch(inv) == "command 'other' != 'cmd'", "Assertion failed"
