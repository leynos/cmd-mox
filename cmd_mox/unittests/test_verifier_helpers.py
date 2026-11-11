"""Unit tests for internal formatting helpers in :mod:`cmd_mox.verifiers`."""

from __future__ import annotations

import types
import typing as typ

import pytest

import cmd_mox.verifiers as v
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation
from cmd_mox.test_doubles import CommandDouble, DoubleKind


def _double(kind: DoubleKind) -> CommandDouble:
    """Return a typed, minimal CommandDouble stub for formatting tests.

    Returns
    -------
    CommandDouble
        A minimal command double with the requested kind.
    """
    return typ.cast("CommandDouble", types.SimpleNamespace(kind=kind))


@pytest.mark.parametrize(
    ("key", "value", "expected"),
    [
        ("API_KEY", "secret", "***"),
        ("token", "value", "***"),
        ("PATH", "value", "value"),
        ("PATH", None, None),
        # The narrower substring vocabulary missed these segment-style keys.
        ("DB_PWD", "secret", "***"),
        ("GITHUB_KEY", "secret", "***"),
        ("SSH_PASSPHRASE", "secret", "***"),
        ("AWS_CREDENTIALS", "secret", "***"),
        ("MONKEY", "value", "value"),
        ("HOME", "/home/test", "/home/test"),
    ],
    ids=[
        "sensitive-uppercase-key",
        "sensitive-lowercase-key",
        "safe-key",
        "safe-key-missing-value",
        "sensitive-pwd-segment",
        "sensitive-key-segment",
        "sensitive-passphrase-segment",
        "sensitive-credentials-segment",
        "benign-near-miss",
        "benign-home",
    ],
)
def test_format_env_masks_with_the_shared_vocabulary(
    key: str, value: str | None, expected: str | None
) -> None:
    """Verifier formatting agrees with the shared classifier for *key*."""
    assert v._format_env({key: value}) == "{" + f"{key!r}: {expected!r}" + "}", (
        "env formatting must mask sensitive keys and preserve safe ones"
    )


def test_format_env_masks_and_sorts_entries() -> None:
    """Environment formatting redacts sensitive values and sorts keys."""
    formatted = v._format_env({"PATH": "/bin", "API_KEY": "secret"})
    assert formatted == "{'API_KEY': '***', 'PATH': '/bin'}", (
        "env formatting must redact secrets and sort keys"
    )


def test_format_env_renders_an_empty_mapping() -> None:
    """An empty environment renders as an empty literal, not a blank line."""
    assert v._format_env({}) == "{}", "an empty env must render as an empty literal"


def test_format_items_handles_empty_and_content() -> None:
    """Item formatting should collapse empty sequences to an empty string."""
    assert v._format_items([]) == "", "an empty sequence must render as an empty string"
    assert v._format_items(None) == "", (
        "a missing sequence must render as an empty string"
    )
    assert v._format_items(["alpha", "beta"]) == "'alpha', 'beta'", (
        "items must be rendered as a comma-separated repr list"
    )


def test_format_call_renders_empty_and_populated_arguments() -> None:
    """Call formatting omits the parentheses' contents when there are no args."""
    assert v._format_call("deploy", "") == "deploy()", (
        "a call without arguments must render empty parentheses"
    )
    assert v._format_call("deploy", "'--flag'") == "deploy('--flag')", (
        "a call with arguments must render them inside the parentheses"
    )


def test_expectation_args_repr_prefers_literal_args() -> None:
    """Literal args win over matchers, and an unconfigured expectation is empty."""
    literal = Expectation("deploy").with_args("--flag")
    matchers = Expectation("deploy").with_matching_args(str.isdigit)
    unconfigured = Expectation("deploy")

    assert v._expectation_args_repr(literal) == "'--flag'", (
        "literal args must be rendered as their repr"
    )
    assert (
        v._expectation_args_repr(matchers) == "<method 'isdigit' of 'str' objects>"
    ), "matcher callables must be rendered as their repr"
    assert v._expectation_args_repr(unconfigured) == "", (
        "an unconfigured expectation must render no arguments"
    )


def test_format_items_formats_matcher_callables() -> None:
    """Matcher callables should be formatted with their repr."""
    assert v._format_items([str.isdigit]) == "<method 'isdigit' of 'str' objects>", (
        "matcher callables must be rendered as their repr"
    )


def test_describe_expectation_renders_all_fields() -> None:
    """Expectation descriptions include args, counts, stdin, and env."""
    exp = Expectation("deploy")
    exp.with_args("--flag")
    exp.with_stdin("payload")
    exp.with_env({"TOKEN": "classified"})
    exp.times_called(2)

    description = v._describe_expectation(exp, include_count=True)
    assert description == (
        "deploy('--flag')\nexpected calls=2\nstdin='payload'\nenv={'TOKEN': '***'}"
    ), "expectation descriptions must include args, counts, stdin, and masked env"


def test_describe_expectation_without_optional_fields() -> None:
    """Expectations without args, stdin, or env render with empty call signature."""
    exp = Expectation("noop")
    assert v._describe_expectation(exp) == "noop()", (
        "an expectation without optional fields must render an empty call signature"
    )


def test_describe_invocation_focuses_requested_env_keys() -> None:
    """Invocation descriptions redact sensitive values and include stdin when asked."""
    inv = Invocation(
        command="deploy",
        args=["--flag"],
        stdin="payload",
        env={"API_KEY": "secret", "PATH": "/bin"},
    )

    description = v._describe_invocation(
        inv,
        focus_env=["API_KEY"],
        include_stdin=True,
    )

    assert description == "deploy('--flag')\nstdin='payload'\nenv={'API_KEY': '***'}", (
        "focused invocation descriptions must include stdin and mask the env"
    )


def test_describe_invocation_omits_optional_sections_by_default() -> None:
    """Without focus_env or include_stdin, only the call is rendered."""
    inv = Invocation(
        command="deploy",
        args=["--flag"],
        stdin="payload",
        env={"API_KEY": "secret"},
    )

    assert v._describe_invocation(inv) == "deploy('--flag')", (
        "the default invocation description must render only the call"
    )


def test_describe_invocation_renders_missing_focused_env_as_none() -> None:
    """A focused key absent from the invocation is rendered as ``None``."""
    inv = Invocation(command="deploy", args=[], stdin="", env={})

    assert v._describe_invocation(inv, focus_env=["TOKEN"]) == (
        "deploy()\nenv={'TOKEN': None}"
    ), "a focused key absent from the invocation must render as None"


def test_describe_invocations_omits_stdin_by_default() -> None:
    """The default description leaves stdin out even when it was recorded."""
    inv = Invocation(command="deploy", args=[], stdin="payload", env={})

    assert v._describe_invocations([inv]) == "deploy()", (
        "the default invocation list must omit stdin"
    )


def test_describe_invocations_handles_empty_and_multiple_entries() -> None:
    """Empty sequences render ``(none)``; entries are joined with newlines."""
    assert v._describe_invocations([]) == "(none)", (
        "an empty invocation list must render as (none)"
    )

    invocations = [
        Invocation(
            command="deploy",
            args=["--one"],
            stdin="a",
            env={"TOKEN": "classified"},
        ),
        Invocation(
            command="deploy",
            args=["--two"],
            stdin="b",
            env={"TOKEN": "classified"},
        ),
    ]

    assert v._describe_invocations(
        invocations,
        focus_env=["TOKEN"],
        include_stdin=True,
    ) == (
        "deploy('--one')\n"
        "stdin='a'\n"
        "env={'TOKEN': '***'}\n"
        "deploy('--two')\n"
        "stdin='b'\n"
        "env={'TOKEN': '***'}"
    ), "multiple invocations must be joined with newlines and mask the env"


def test_numbered_renders_multiline_entries() -> None:
    """Numbered helper should indent continuation lines."""
    entries = ["first line", "second\nwith extra"]
    assert v._numbered(entries) == "1. first line\n2. second\n   with extra", (
        "numbered entries must indent continuation lines"
    )


def test_numbered_handles_empty_entries_and_custom_start() -> None:
    """Empty sequences render ``(none)``; numbering can start above one."""
    assert v._numbered([]) == "(none)", "an empty sequence must render as (none)"
    assert v._numbered([""]) == "1. ", "an empty entry must render its number only"
    assert v._numbered(["alpha", "beta"], start=3) == "3. alpha\n4. beta", (
        "numbering must honour a custom start"
    )


def test_format_sections_omits_empty_sections() -> None:
    """Formatting helper should skip blank section bodies."""
    body = v._format_sections(
        "Header",
        [("Empty", ""), ("Details", "line1\nline2")],
    )

    assert body == "Header\n\nDetails:\n  line1\n  line2", (
        "empty sections must be omitted and populated ones indented"
    )


def test_format_sections_renders_only_the_title_when_all_empty() -> None:
    """A message with no populated sections is just its title."""
    assert v._format_sections("Header", [("Empty", "")]) == "Header", (
        "a message with no populated sections must render only its title"
    )


def test_list_expected_commands_excludes_stubs() -> None:
    """Helper lists only commands that can raise unexpected-invocation errors."""
    doubles = {
        "alpha": _double(DoubleKind.MOCK),
        "beta": _double(DoubleKind.STUB),
        "gamma": _double(DoubleKind.SPY),
    }

    assert v._list_expected_commands(doubles) == "'alpha', 'gamma'", (
        "expected commands must exclude stubs and include mocks and spies"
    )


def test_list_expected_commands_reports_when_only_stubs() -> None:
    """When only stubs are registered, the helper documents the omission."""
    doubles = {
        "alpha": _double(DoubleKind.STUB),
    }

    assert (
        v._list_expected_commands(doubles)
        == "(none: stubs are excluded from expected commands)"
    ), "a registry of only stubs must document the omission"
