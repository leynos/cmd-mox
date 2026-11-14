"""Render verification diagnostics as the text users read in an error.

The verifiers decide what to report; these helpers turn that decision into
numbered, labelled sections. Keeping the rendering apart leaves the redaction
rules in one place and the verifier classes focused on verification policy.
"""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from collections import defaultdict
from textwrap import indent

from ._argument_redaction import (
    REDACTED_ARGUMENT,
    mask_sensitive_positions,
    redact_sensitive_argument_values,
)
from .domain import mask_env_value
from .test_doubles import DoubleKind

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from .expectations import Expectation
    from .ipc import Invocation
    from .test_doubles import CommandDouble


def _format_env(mapping: cabc.Mapping[str, str | None]) -> str:
    """Return a deterministic representation of environment values.

    Parameters
    ----------
    mapping : collections.abc.Mapping
        Environment values to sort and format.

    Returns
    -------
    str
        Stable representation with sensitive values masked.
    """
    if not mapping:
        return "{}"
    parts = []
    for key in sorted(mapping):
        masked = mask_env_value(key, mapping[key])
        parts.append(f"{key!r}: {masked!r}")
    return "{" + ", ".join(parts) + "}"


def _format_items[ItemT](items: cabc.Sequence[ItemT] | None) -> str:
    """Format *items* as a comma-separated representation.

    Parameters
    ----------
    items : collections.abc.Sequence or None
        Values to format, such as command arguments or matcher predicates.

    Returns
    -------
    str
        Repr-formatted items, or an empty string when absent.
    """
    return "" if not items else ", ".join(repr(item) for item in items)


def _format_call(name: str, args_repr: str) -> str:
    """Format a command name and preformatted arguments as a call.

    Returns
    -------
    str
        Call-like representation such as ``name('arg')``.
    """
    return f"{name}({args_repr})" if args_repr else f"{name}()"


def _redact_expected_items(
    items: cabc.Sequence[object],
    *,
    command: str,
    actual_calls: cabc.Sequence[cabc.Sequence[str]] | None = None,
) -> list[object]:
    """Return *items* with sensitive literal strings redacted.

    Only string entries can carry a credential; matcher callables are
    rendered unchanged. Every set of arguments in *actual_calls* contributes
    the positions it marks as sensitive, because a predicate matcher can hide
    the option name that would otherwise identify the following credential.
    Masking accumulates, so an earlier match keeps its placeholder.

    Returns
    -------
    list[object]
        Items with sensitive values masked and positions preserved.
    """
    literals = [item if isinstance(item, str) else "" for item in items]
    redacted = redact_sensitive_argument_values(literals, command=command)
    for args in actual_calls or ():
        redacted = mask_sensitive_positions(redacted, list(args), command=command)
    return [
        safe if isinstance(item, str) or safe == REDACTED_ARGUMENT else item
        for item, safe in zip(items, redacted, strict=True)
    ]


def _masked_call_arguments(
    invocations: cabc.Sequence[Invocation],
) -> list[list[object]]:
    """Mask each call's arguments from every call to the same command."""
    peers: dict[str, list[list[str]]] = defaultdict(list)
    for inv in invocations:
        peers[inv.command].append(list(inv.args))
    return [
        _redact_expected_items(
            list(inv.args), command=inv.command, actual_calls=peers[inv.command]
        )
        for inv in invocations
    ]


def _expectation_args_repr(
    exp: Expectation, *, actual_calls: cabc.Sequence[cabc.Sequence[str]] | None = None
) -> str:
    """Return the configured argument or matcher representation.

    Expected values may carry credentials, so they are redacted with the same
    rules applied to actual invocations, including the positions those calls
    mark as sensitive.

    Returns
    -------
    str
        Representation of exact arguments, matchers, or no arguments.
    """
    if exp.args is not None:
        return _format_items(
            _redact_expected_items(
                exp.args, command=exp.name, actual_calls=actual_calls
            )
        )
    if exp.match_args is not None:
        return _format_items(
            _redact_expected_items(
                exp.match_args, command=exp.name, actual_calls=actual_calls
            )
        )
    return ""


def _append_count_line(
    lines: list[str], exp: Expectation, *, include_count: bool
) -> None:
    """Append the expected call count line when *include_count* is set."""
    if include_count:
        lines.append(f"expected calls={exp.count}")


def _append_stdin_line(lines: list[str], exp: Expectation) -> None:
    """Append the expected stdin line when the expectation sets stdin."""
    if exp.stdin is not None:
        lines.append(f"stdin={exp.stdin!r}")


def _append_env_line(lines: list[str], exp: Expectation) -> None:
    """Append the expected environment line when the expectation sets env."""
    if exp.env:
        lines.append(f"env={_format_env(exp.env)}")


def _describe_expectation(
    exp: Expectation,
    *,
    include_count: bool = False,
    include_stdin: bool = True,
    actual_calls: cabc.Sequence[cabc.Sequence[str]] | None = None,
) -> str:
    """Return a human-readable representation of *exp*.

    Parameters
    ----------
    exp : Expectation
        Expectation to describe.
    include_count : bool
        Whether to include the expected call count.
    include_stdin : bool
        Whether to include the expected stdin value. Diagnostics that must
        never echo stdin content pass ``False``.
    actual_calls : collections.abc.Sequence of collections.abc.Sequence of str or None
        Arguments of the calls being compared, when any exist. Their sensitive
        positions also mask the corresponding expected values.

    Returns
    -------
    str
        Multi-line diagnostic description.
    """
    lines = [
        _format_call(exp.name, _expectation_args_repr(exp, actual_calls=actual_calls))
    ]
    _append_count_line(lines, exp, include_count=include_count)
    if include_stdin:
        _append_stdin_line(lines, exp)
    _append_env_line(lines, exp)
    return "\n".join(lines)


def _describe_invocation(
    inv: Invocation,
    *,
    focus_env: cabc.Iterable[str] | None = None,
    include_stdin: bool = False,
    safe_args: cabc.Sequence[object] | None = None,
) -> str:
    """Return a readable representation of *inv*.

    Parameters
    ----------
    inv : Invocation
        Invocation to describe.
    focus_env : collections.abc.Iterable[str] or None
        Environment keys to include, when supplied.
    include_stdin : bool
        Whether to include stdin in the description.
    safe_args : collections.abc.Sequence of object or None
        Arguments already masked against an expectation. When omitted, only
        the invocation's own option names decide what to redact.

    Returns
    -------
    str
        Multi-line diagnostic description.
    """
    if safe_args is None:
        safe_args = redact_sensitive_argument_values(
            list(inv.args), command=inv.command
        )
    lines = [_format_call(inv.command, _format_items(safe_args))]
    if include_stdin:
        lines.append(f"stdin={inv.stdin!r}")
    if focus_env:
        env_subset = {key: inv.env.get(key) for key in sorted(focus_env)}
        lines.append(f"env={_format_env(env_subset)}")
    return "\n".join(lines)


def _describe_actual_call(
    exp: Expectation,
    inv: Invocation,
    *,
    include_stdin: bool | None = None,
    safe_args: cabc.Sequence[object] | None = None,
) -> str:
    """Describe *inv*, redacted against *exp* and any supplied safe arguments."""
    show_stdin = exp.stdin is not None if include_stdin is None else include_stdin
    return _describe_invocation(
        inv,
        focus_env=exp.env.keys(),
        include_stdin=show_stdin,
        safe_args=exp.redacted_arguments(inv.args) if safe_args is None else safe_args,
    )


def _describe_invocations(
    invocations: cabc.Sequence[Invocation],
    *,
    focus_env: cabc.Iterable[str] | None = None,
    include_stdin: bool = False,
) -> str:
    """Describe a sequence of invocations, or ``(none)`` when empty.

    Returns
    -------
    str
        Multi-line diagnostic description.
    """
    if not invocations:
        return "(none)"
    return "\n".join(
        _describe_invocation(inv, focus_env=focus_env, include_stdin=include_stdin)
        for inv in invocations
    )


def _numbered(entries: cabc.Sequence[str], *, start: int = 1) -> str:
    """Prefix entries with sequential numbers for diagnostic output.

    Returns
    -------
    str
        Numbered entries, or ``(none)`` when empty.
    """
    if not entries:
        return "(none)"
    lines: list[str] = []
    for index, entry in enumerate(entries, start=start):
        entry_lines = entry.splitlines() or [""]
        lines.append(f"{index}. {entry_lines[0]}")
        lines.extend(f"   {extra}" for extra in entry_lines[1:])
    return "\n".join(lines)


def _format_sections(title: str, sections: list[tuple[str, str]]) -> str:
    """Assemble a titled diagnostic message from labelled sections.

    Returns
    -------
    str
        Formatted message with empty sections omitted.
    """
    parts = [title]
    for label, body in sections:
        if not body:
            continue
        parts.extend(("", f"{label}:", indent(body, "  ")))
    return "\n".join(parts)


def _list_expected_commands(doubles: cabc.Mapping[str, CommandDouble]) -> str:
    """Return a readable list of registered, invokable commands.

    Commands registered as stubs are omitted because they are not validated as
    part of unexpected-invocation checks.

    Returns
    -------
    str
        A comma-separated representation of the expected commands.
    """
    names = sorted(
        name for name, dbl in doubles.items() if dbl.kind is not DoubleKind.STUB
    )
    return (
        "(none: stubs are excluded from expected commands)"
        if not names
        else ", ".join(repr(name) for name in names)
    )
