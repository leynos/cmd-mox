"""Verification helpers for :class:`CmdMox`."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from collections import defaultdict
from textwrap import indent

from .domain import mask_env_value
from .errors import UnexpectedCommandError, UnfulfilledExpectationError
from .test_doubles import DoubleKind

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from .controller import CommandDouble
    from .expectations import Expectation
    from .ipc import Invocation


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


def _expectation_args_repr(exp: Expectation) -> str:
    """Return the configured argument or matcher representation.

    Returns
    -------
    str
        Representation of exact arguments, matchers, or no arguments.
    """
    if exp.args is not None:
        return _format_items(exp.args)
    if exp.match_args is not None:
        return _format_items(exp.match_args)
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


def _describe_expectation(exp: Expectation, *, include_count: bool = False) -> str:
    """Return a human-readable representation of *exp*.

    Parameters
    ----------
    exp : Expectation
        Expectation to describe.
    include_count : bool
        Whether to include the expected call count.

    Returns
    -------
    str
        Multi-line diagnostic description.
    """
    lines = [_format_call(exp.name, _expectation_args_repr(exp))]
    _append_count_line(lines, exp, include_count=include_count)
    _append_stdin_line(lines, exp)
    _append_env_line(lines, exp)
    return "\n".join(lines)


def _describe_invocation(
    inv: Invocation,
    *,
    focus_env: cabc.Iterable[str] | None = None,
    include_stdin: bool = False,
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

    Returns
    -------
    str
        Multi-line diagnostic description.
    """
    lines = [_format_call(inv.command, _format_items(inv.args))]
    if include_stdin:
        lines.append(f"stdin={inv.stdin!r}")
    if focus_env:
        env_subset = {key: inv.env.get(key) for key in sorted(focus_env)}
        lines.append(f"env={_format_env(env_subset)}")
    return "\n".join(lines)


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


class UnexpectedCommandVerifier:
    """Check invocations match registered expectations."""

    def verify(
        self,
        journal: cabc.Iterable[Invocation],
        doubles: cabc.Mapping[str, CommandDouble],
        dispatch_doubles: cabc.Iterable[CommandDouble | None] | None = None,
    ) -> None:
        """Raise if *journal* contains calls not matching registered doubles.

        ``dispatch_doubles`` aligns with *journal* and records the double each
        call was actually dispatched to, so a double registered later cannot
        retroactively claim an earlier call. When omitted, the current mapping
        is used for every call.
        """
        invocations = list(journal)
        if dispatch_doubles is None:
            dispatch_doubles = [doubles.get(inv.command) for inv in invocations]
        mock_counts: dict[str, int] = defaultdict(int)
        for inv, dbl in zip(invocations, dispatch_doubles, strict=True):
            self._process_single_invocation(inv, dbl, doubles, mock_counts)

    def _process_single_invocation(
        self,
        inv: Invocation,
        dbl: CommandDouble | None,
        doubles: cabc.Mapping[str, CommandDouble],
        mock_counts: dict[str, int],
    ) -> None:
        """Check one invocation against the double it was dispatched to."""
        if dbl is None:
            self._raise_unregistered_command_error(inv, doubles)
            return

        if dbl.kind is DoubleKind.STUB:
            return

        self._validate_expectation_match(inv, dbl, mock_counts)

    def _validate_expectation_match(
        self,
        inv: Invocation,
        dbl: CommandDouble,
        mock_counts: dict[str, int],
    ) -> None:
        """Check that an invocation matches its expectation and mock limits."""
        exp = dbl.expectation
        if not exp.matches(inv):
            self._raise_expectation_mismatch_error(exp, inv)
            return

        if dbl.kind is DoubleKind.MOCK:
            self._check_mock_call_count(dbl, exp, inv, mock_counts)

    def _check_mock_call_count(
        self,
        dbl: CommandDouble,
        exp: Expectation,
        inv: Invocation,
        mock_counts: dict[str, int],
    ) -> None:
        """Count a mock call and raise when it exceeds the expected count."""
        mock_counts[dbl.name] += 1
        observed = mock_counts[dbl.name]
        if observed <= exp.count:
            return

        self._raise_mock_overflow_error(exp, inv, observed)

    @staticmethod
    def _raise_unregistered_command_error(
        inv: Invocation,
        doubles: cabc.Mapping[str, CommandDouble],
    ) -> None:
        """Raise for an invocation of a command that has no registered double."""
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

    @staticmethod
    def _raise_expectation_mismatch_error(
        exp: Expectation,
        inv: Invocation,
    ) -> None:
        """Raise for an invocation that does not match its expectation."""
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

    @staticmethod
    def _raise_mock_overflow_error(
        exp: Expectation,
        inv: Invocation,
        observed: int,
    ) -> None:
        """Raise for a mock invoked more times than its expected count."""
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
        """Store the expectations that must appear in the given order."""
        self._ordered = ordered
        self._current_expected_descriptions: list[str] = []
        self._current_actual_descriptions: list[str] = []

    def verify(self, journal: cabc.Iterable[Invocation]) -> None:
        """Ensure ordered expectations appear in order within *journal*."""
        ordered_seq = self._build_ordered_sequence()
        if not ordered_seq:
            return

        relevant_invocations = self._get_relevant_invocations(journal, ordered_seq)
        self._validate_expectations_order(ordered_seq, relevant_invocations)

    def _build_ordered_sequence(self) -> list[Expectation]:
        """Expand ordered expectations into one entry per expected call."""
        ordered_seq: list[Expectation] = []
        for exp in self._ordered:
            ordered_seq.extend([exp] * exp.count)
        return ordered_seq

    @staticmethod
    def _get_relevant_invocations(
        journal: cabc.Iterable[Invocation],
        ordered_seq: list[Expectation],
    ) -> list[Invocation]:
        # Ignore invocations that do not satisfy any ordered expectation so
        # unordered calls of the same command name do not trigger spurious
        # ordering failures.
        """Return invocations that satisfy at least one ordered expectation."""
        return [inv for inv in journal if any(exp.matches(inv) for exp in ordered_seq)]

    def _validate_expectations_order(
        self,
        ordered_seq: list[Expectation],
        relevant_invocations: list[Invocation],
    ) -> None:
        """Check the relevant journal against the expanded ordered sequence."""
        expected_descriptions = [_describe_expectation(exp) for exp in ordered_seq]
        actual_descriptions = [
            _describe_invocation(inv) for inv in relevant_invocations
        ]
        self._current_expected_descriptions = expected_descriptions
        self._current_actual_descriptions = actual_descriptions

        self._check_missing_expectations(
            ordered_seq,
            relevant_invocations,
            expected_descriptions,
            actual_descriptions,
        )
        self._check_order_violations(ordered_seq, relevant_invocations)
        self._check_extra_invocations(
            ordered_seq,
            relevant_invocations,
            expected_descriptions,
            actual_descriptions,
        )

    @staticmethod
    def _check_missing_expectations(
        ordered_seq: list[Expectation],
        relevant_invocations: list[Invocation],
        expected_descriptions: list[str],
        actual_descriptions: list[str],
    ) -> None:
        """Raise when fewer invocations were observed than ordered expectations."""
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
    ) -> None:
        """Raise on the first invocation that breaks the expected order."""
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
            )
            return

    @staticmethod
    def _check_extra_invocations(
        ordered_seq: list[Expectation],
        relevant_invocations: list[Invocation],
        expected_descriptions: list[str],
        actual_descriptions: list[str],
    ) -> None:
        """Raise when more invocations were observed than ordered expectations."""
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
    ) -> None:
        """Raise a detailed error for the first out-of-order invocation."""
        mismatch = "\n".join([
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
        ])
        msg = _format_sections(
            "Ordered expectation violated.",
            [
                (
                    "Expected order",
                    _numbered(self._current_expected_descriptions),
                ),
                (
                    "Observed order",
                    _numbered(self._current_actual_descriptions),
                ),
                ("First mismatch", mismatch),
                ("Reason", reason),
            ],
        )
        raise UnexpectedCommandError(msg)


def build_count_map(
    expectations: cabc.Mapping[str, Expectation],
    doubles: cabc.Mapping[str, CommandDouble],
    dispatched: cabc.Sequence[tuple[Invocation, CommandDouble | None]],
    unrecorded: cabc.Iterable[tuple[CommandDouble, Invocation]],
) -> dict[str, list[Invocation]]:
    """Combine recorded and unrecorded calls for count verification.

    Passthrough calls are registered before their result is recorded, so a
    call can be in flight, or can have expired, when verification runs. The
    caller snapshots the unrecorded registry before the completed-call log;
    a call leaves the registry only after it is appended to the log, so the
    snapshots together contain every call. A call that completes between the
    snapshots appears in both, so calls are de-duplicated by invocation ID.

    Parameters
    ----------
    expectations : collections.abc.Mapping
        Expectations to check, keyed by command name.
    doubles : collections.abc.Mapping
        Registered doubles keyed by command name.
    dispatched : collections.abc.Sequence
        Completed invocations paired with the double that dispatched them.
    unrecorded : collections.abc.Iterable
        Registered invocations the controller has not recorded, paired with
        their owning double.

    Returns
    -------
    dict[str, list[Invocation]]
        Every call counted exactly once, keyed by command name.
    """
    inv_map: dict[str, list[Invocation]] = {}
    for name in expectations:
        double = doubles[name]
        recorded = [inv for inv, dbl in dispatched if dbl is double]
        seen = {inv.invocation_id for inv in recorded}
        inv_map[name] = recorded + [
            inv
            for pending_double, inv in unrecorded
            if pending_double is double and inv.invocation_id not in seen
        ]
    return inv_map


class CountVerifier:
    """Check that each expectation was met the expected number of times."""

    @staticmethod
    def verify(
        expectations: cabc.Mapping[str, Expectation],
        invocations: cabc.Mapping[str, list[Invocation]],
    ) -> None:
        """Validate invocation counts against ``expectations``.

        Parameters
        ----------
        expectations : collections.abc.Mapping
            Registered expectations keyed by command name.
        invocations : collections.abc.Mapping
            Recorded invocations keyed by command name.

        Raises
        ------
        UnfulfilledExpectationError
            If a command has fewer invocations than expected.
        UnexpectedCommandError
            If a command has more invocations than expected.
        """
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
