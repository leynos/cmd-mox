"""Verification helpers for :class:`CmdMox`."""

from __future__ import annotations

import typing as t
from collections import defaultdict
from textwrap import indent

from .errors import UnexpectedCommandError, UnfulfilledExpectationError
from .expectations import SENSITIVE_ENV_KEY_TOKENS

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from .controller import CommandDouble
    from .expectations import Expectation
    from .ipc import Invocation

_SENSITIVE_TOKENS: tuple[str, ...] = tuple(
    token.casefold() for token in SENSITIVE_ENV_KEY_TOKENS
)


def _mask_env_value(key: str, value: str | None) -> str | None:
    """Redact *value* when *key* appears sensitive."""
    if value is None:
        return None
    key_cf = key.casefold()
    if any(token in key_cf for token in _SENSITIVE_TOKENS):
        return "***"
    return value


def _format_env(mapping: t.Mapping[str, str | None]) -> str:
    """Return a deterministic representation of environment values."""
    if not mapping:
        return "{}"
    parts = []
    for key in sorted(mapping):
        masked = _mask_env_value(key, mapping[key])
        parts.append(f"{key!r}: {masked!r}")
    return "{" + ", ".join(parts) + "}"


def _format_args(args: t.Sequence[str] | None) -> str:
    if not args:
        return ""
    return ", ".join(repr(arg) for arg in args)


def _format_matchers(matchers: t.Sequence[t.Callable[[str], bool]] | None) -> str:
    if not matchers:
        return ""
    return ", ".join(repr(matcher) for matcher in matchers)


def _format_call(name: str, args_repr: str) -> str:
    return f"{name}({args_repr})" if args_repr else f"{name}()"


def _expectation_args_repr(exp: Expectation) -> str:
    if exp.args is not None:
        return _format_args(exp.args)
    if exp.match_args is not None:
        return _format_matchers(exp.match_args)
    return ""


def _append_count_line(
    lines: list[str], exp: Expectation, *, include_count: bool
) -> None:
    if include_count:
        lines.append(f"expected calls={exp.count}")


def _append_stdin_line(lines: list[str], exp: Expectation) -> None:
    if exp.stdin is not None:
        lines.append(f"stdin={exp.stdin!r}")


def _append_env_line(lines: list[str], exp: Expectation) -> None:
    if exp.env:
        lines.append(f"env={_format_env(exp.env)}")


def _describe_expectation(exp: Expectation, *, include_count: bool = False) -> str:
    """Return a human readable representation of *exp*."""
    lines = [_format_call(exp.name, _expectation_args_repr(exp))]
    _append_count_line(lines, exp, include_count=include_count)
    _append_stdin_line(lines, exp)
    _append_env_line(lines, exp)
    return "\n".join(lines)


def _describe_invocation(
    inv: Invocation,
    *,
    focus_env: t.Iterable[str] | None = None,
    include_stdin: bool = False,
) -> str:
    """Return a readable representation of *inv*."""
    lines = [_format_call(inv.command, _format_args(inv.args))]
    if include_stdin:
        lines.append(f"stdin={inv.stdin!r}")
    if focus_env:
        env_subset = {key: inv.env.get(key) for key in sorted(focus_env)}
        lines.append(f"env={_format_env(env_subset)}")
    return "\n".join(lines)


def _describe_invocations(
    invocations: t.Sequence[Invocation],
    *,
    focus_env: t.Iterable[str] | None = None,
    include_stdin: bool = False,
) -> str:
    if not invocations:
        return "(none)"
    return "\n".join(
        _describe_invocation(inv, focus_env=focus_env, include_stdin=include_stdin)
        for inv in invocations
    )


def _numbered(entries: t.Sequence[str], *, start: int = 1) -> str:
    if not entries:
        return "(none)"
    lines: list[str] = []
    for index, entry in enumerate(entries, start=start):
        entry_lines = entry.splitlines() or [""]
        lines.append(f"{index}. {entry_lines[0]}")
        lines.extend(f"   {extra}" for extra in entry_lines[1:])
    return "\n".join(lines)


def _format_sections(title: str, sections: list[tuple[str, str]]) -> str:
    parts = [title]
    for label, body in sections:
        if not body:
            continue
        parts.append("")
        parts.append(f"{label}:")
        parts.append(indent(body, "  "))
    return "\n".join(parts)


def _list_expected_commands(doubles: t.Mapping[str, CommandDouble]) -> str:
    names = sorted(name for name, dbl in doubles.items() if dbl.kind != "stub")
    if not names:
        return "(none)"
    return ", ".join(repr(name) for name in names)


class UnexpectedCommandVerifier:
    """Check invocations match registered expectations."""

    def verify(
        self,
        journal: t.Iterable[Invocation],
        doubles: t.Mapping[str, CommandDouble],
    ) -> None:
        """Raise if *journal* contains calls not matching registered doubles."""
        mock_counts: dict[str, int] = defaultdict(int)
        for inv in journal:
            self._process_single_invocation(inv, doubles, mock_counts)

    def _process_single_invocation(
        self,
        inv: Invocation,
        doubles: t.Mapping[str, CommandDouble],
        mock_counts: dict[str, int],
    ) -> None:
        dbl = doubles.get(inv.command)
        if dbl is None:
            self._raise_unregistered_command_error(inv, doubles)
            return

        if dbl.kind == "stub":
            return

        self._validate_expectation_match(inv, dbl, mock_counts)

    def _validate_expectation_match(
        self,
        inv: Invocation,
        dbl: CommandDouble,
        mock_counts: dict[str, int],
    ) -> None:
        exp = dbl.expectation
        if not exp.matches(inv):
            self._raise_expectation_mismatch_error(exp, inv)
            return

        if dbl.kind == "mock":
            self._check_mock_call_count(dbl, exp, inv, mock_counts)

    def _check_mock_call_count(
        self,
        dbl: CommandDouble,
        exp: Expectation,
        inv: Invocation,
        mock_counts: dict[str, int],
    ) -> None:
        mock_counts[dbl.name] += 1
        observed = mock_counts[dbl.name]
        if observed <= exp.count:
            return

        self._raise_mock_overflow_error(exp, inv, observed)

    def _raise_unregistered_command_error(
        self,
        inv: Invocation,
        doubles: t.Mapping[str, CommandDouble],
    ) -> None:
        msg = _format_sections(
            "Unexpected command invocation.",
            [
                (
                    "Actual call",
                    _describe_invocation(inv, include_stdin=bool(inv.stdin)),
                ),
                ("Registered expectations", _list_expected_commands(doubles)),
            ],
        )
        raise UnexpectedCommandError(msg)

    def _raise_expectation_mismatch_error(
        self,
        exp: Expectation,
        inv: Invocation,
    ) -> None:
        reason = exp.explain_mismatch(inv)
        msg = _format_sections(
            "Unexpected command invocation.",
            [
                ("Expected", _describe_expectation(exp)),
                (
                    "Actual",
                    _describe_invocation(
                        inv,
                        focus_env=exp.env.keys(),
                        include_stdin=exp.stdin is not None,
                    ),
                ),
                ("Reason", reason),
            ],
        )
        raise UnexpectedCommandError(msg)

    def _raise_mock_overflow_error(
        self,
        exp: Expectation,
        inv: Invocation,
        observed: int,
    ) -> None:
        msg = _format_sections(
            "Unexpected additional invocation.",
            [
                ("Expected", _describe_expectation(exp, include_count=True)),
                ("Observed calls", str(observed)),
                (
                    "Last call",
                    _describe_invocation(
                        inv,
                        focus_env=exp.env.keys(),
                        include_stdin=exp.stdin is not None,
                    ),
                ),
            ],
        )
        raise UnexpectedCommandError(msg)


class OrderVerifier:
    """Validate ordering of expectations marked with ``in_order``."""

    def __init__(self, ordered: list[Expectation]) -> None:
        self._ordered = ordered

    def verify(self, journal: t.Iterable[Invocation]) -> None:
        """Ensure ordered expectations appear in order within *journal*."""
        ordered_seq = self._build_ordered_sequence()
        if not ordered_seq:
            return

        relevant_invocations = self._get_relevant_invocations(journal, ordered_seq)
        self._validate_expectations_order(ordered_seq, relevant_invocations)

    def _build_ordered_sequence(self) -> list[Expectation]:
        ordered_seq: list[Expectation] = []
        for exp in self._ordered:
            ordered_seq.extend([exp] * exp.count)
        return ordered_seq

    def _get_relevant_invocations(
        self,
        journal: t.Iterable[Invocation],
        ordered_seq: list[Expectation],
    ) -> list[Invocation]:
        relevant_commands = {exp.name for exp in ordered_seq}
        return [inv for inv in journal if inv.command in relevant_commands]

    def _validate_expectations_order(
        self,
        ordered_seq: list[Expectation],
        relevant_invocations: list[Invocation],
    ) -> None:
        expected_descriptions = [_describe_expectation(exp) for exp in ordered_seq]
        actual_descriptions = [
            _describe_invocation(inv) for inv in relevant_invocations
        ]

        self._check_missing_expectations(
            ordered_seq,
            relevant_invocations,
            expected_descriptions,
            actual_descriptions,
        )
        self._check_order_violations(
            ordered_seq,
            relevant_invocations,
            expected_descriptions,
            actual_descriptions,
        )
        self._check_extra_invocations(
            ordered_seq,
            relevant_invocations,
            expected_descriptions,
            actual_descriptions,
        )

    def _check_missing_expectations(
        self,
        ordered_seq: list[Expectation],
        relevant_invocations: list[Invocation],
        expected_descriptions: list[str],
        actual_descriptions: list[str],
    ) -> None:
        if len(relevant_invocations) >= len(ordered_seq):
            return

        index = len(relevant_invocations)
        remaining = _numbered(expected_descriptions[index:], start=index + 1)
        msg = _format_sections(
            "Ordered expectations not satisfied.",
            [
                ("Expected order", _numbered(expected_descriptions)),
                ("Observed order", _numbered(actual_descriptions)),
                ("Missing", remaining),
            ],
        )
        raise UnfulfilledExpectationError(msg)

    def _check_order_violations(
        self,
        ordered_seq: list[Expectation],
        relevant_invocations: list[Invocation],
        expected_descriptions: list[str],
        actual_descriptions: list[str],
    ) -> None:
        for index, (exp, actual_inv) in enumerate(
            zip(ordered_seq, relevant_invocations, strict=False)
        ):
            if exp.matches(actual_inv):
                continue

            reason = exp.explain_mismatch(actual_inv)
            self._raise_mismatch_error(
                index,
                exp,
                actual_inv,
                reason,
                expected_descriptions,
                actual_descriptions,
            )
            return

    def _check_extra_invocations(
        self,
        ordered_seq: list[Expectation],
        relevant_invocations: list[Invocation],
        expected_descriptions: list[str],
        actual_descriptions: list[str],
    ) -> None:
        if len(relevant_invocations) <= len(ordered_seq):
            return

        extras = relevant_invocations[len(ordered_seq) :]
        msg = _format_sections(
            "Unexpected additional invocation.",
            [
                ("Expected order", _numbered(expected_descriptions)),
                ("Observed order", _numbered(actual_descriptions)),
                (
                    "Unexpected calls",
                    _describe_invocations(extras, include_stdin=False),
                ),
            ],
        )
        raise UnexpectedCommandError(msg)

    def _raise_mismatch_error(
        self,
        index: int,
        exp: Expectation,
        actual_inv: Invocation,
        reason: str,
        expected_descriptions: list[str],
        actual_descriptions: list[str],
    ) -> None:
        mismatch = "\n".join(
            [
                f"position {index + 1}",
                "expected:\n"
                + indent(
                    _describe_expectation(exp),
                    "  ",
                ),
                "actual:\n"
                + indent(
                    _describe_invocation(
                        actual_inv,
                        focus_env=exp.env.keys(),
                        include_stdin=exp.stdin is not None,
                    ),
                    "  ",
                ),
            ]
        )
        msg = _format_sections(
            "Ordered expectation violated.",
            [
                ("Expected order", _numbered(expected_descriptions)),
                ("Observed order", _numbered(actual_descriptions)),
                ("First mismatch", mismatch),
                ("Reason", reason),
            ],
        )
        raise UnexpectedCommandError(msg)


class CountVerifier:
    """Check that each expectation was met the expected number of times."""

    def verify(
        self,
        expectations: t.Mapping[str, Expectation],
        invocations: t.Mapping[str, list[Invocation]],
    ) -> None:
        """Validate invocation counts against ``expectations``."""
        for name, exp in expectations.items():
            calls = invocations.get(name, [])
            actual = len(calls)
            expected = exp.count
            focus_env = exp.env.keys()
            include_stdin = exp.stdin is not None
            if actual < expected:
                msg = _format_sections(
                    "Unfulfilled expectation.",
                    [
                        (
                            "Expected",
                            _describe_expectation(exp, include_count=True),
                        ),
                        ("Observed calls", f"{actual} (expected {expected})"),
                        (
                            "Recorded invocations",
                            _describe_invocations(
                                calls,
                                focus_env=focus_env,
                                include_stdin=include_stdin,
                            ),
                        ),
                    ],
                )
                raise UnfulfilledExpectationError(msg)
            if actual > expected:
                msg = _format_sections(
                    "Unexpected additional invocation.",
                    [
                        (
                            "Expected",
                            _describe_expectation(exp, include_count=True),
                        ),
                        ("Observed calls", f"{actual} (expected {expected})"),
                        (
                            "Last call",
                            _describe_invocation(
                                calls[-1],
                                focus_env=focus_env,
                                include_stdin=include_stdin,
                            ),
                        ),
                    ],
                )
                raise UnexpectedCommandError(msg)
