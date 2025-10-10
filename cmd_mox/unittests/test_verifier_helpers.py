"""Unit tests for internal formatting helpers in :mod:`cmd_mox.verifiers`."""

from __future__ import annotations

import types
import typing as t

import cmd_mox.verifiers as v
from cmd_mox.expectations import Expectation
from cmd_mox.ipc import Invocation
from cmd_mox.test_doubles import CommandDouble, DoubleKind


def _double(kind: DoubleKind) -> CommandDouble:
    """Return a typed, minimal CommandDouble stub for formatting tests."""
    return t.cast("CommandDouble", types.SimpleNamespace(kind=kind))


def test_mask_env_value_redacts_sensitive_keys() -> None:
    """Sensitive environment keys should be obscured."""
    assert v._mask_env_value("API_KEY", "secret") == "***"
    assert v._mask_env_value("token", "value") == "***"


def test_mask_env_value_preserves_safe_values() -> None:
    """Safe keys or missing values remain untouched."""
    assert v._mask_env_value("PATH", "value") == "value"
    assert v._mask_env_value("PATH", None) is None


def test_format_env_masks_and_sorts_entries() -> None:
    """Environment formatting redacts sensitive values and sorts keys."""
    formatted = v._format_env({"PATH": "/bin", "API_KEY": "secret"})
    assert formatted == "{'API_KEY': '***', 'PATH': '/bin'}"


def test_format_args_handles_empty_and_content() -> None:
    """Argument formatting should collapse empty sequences to an empty string."""
    assert v._format_args([]) == ""
    assert v._format_args(["alpha", "beta"]) == "'alpha', 'beta'"


def test_format_matchers_handles_empty_and_content() -> None:
    """Matcher formatting should mirror argument formatting semantics."""
    assert v._format_matchers([]) == ""
    assert v._format_matchers([str.isdigit]) == "<method 'isdigit' of 'str' objects>"


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
    )


def test_describe_expectation_without_optional_fields() -> None:
    """Expectations without args, stdin, or env render with empty call signature."""
    exp = Expectation("noop")
    assert v._describe_expectation(exp) == "noop()"


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

    assert description == "deploy('--flag')\nstdin='payload'\nenv={'API_KEY': '***'}"


def test_numbered_renders_multiline_entries() -> None:
    """Numbered helper should indent continuation lines."""
    entries = ["first line", "second\nwith extra"]
    assert v._numbered(entries) == "1. first line\n2. second\n   with extra"


def test_format_sections_omits_empty_sections() -> None:
    """Formatting helper should skip blank section bodies."""
    body = v._format_sections(
        "Header",
        [("Empty", ""), ("Details", "line1\nline2")],
    )

    assert body == "Header\n\nDetails:\n  line1\n  line2"


def test_list_expected_commands_excludes_stubs() -> None:
    """Helper lists only commands that can raise unexpected-invocation errors."""
    doubles = {
        "alpha": _double(DoubleKind.MOCK),
        "beta": _double(DoubleKind.STUB),
        "gamma": _double(DoubleKind.SPY),
    }

    assert v._list_expected_commands(doubles) == "'alpha', 'gamma'"


def test_list_expected_commands_reports_when_only_stubs() -> None:
    """When only stubs are registered, the helper documents the omission."""
    doubles = {
        "alpha": _double(DoubleKind.STUB),
    }

    assert (
        v._list_expected_commands(doubles)
        == "(none: stubs are excluded from expected commands)"
    )
