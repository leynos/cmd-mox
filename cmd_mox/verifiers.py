"""Verification helpers for :class:`CmdMox`."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from collections import defaultdict
from textwrap import indent

from ._diagnostic_format import (
    _describe_actual_call,
    _describe_expectation,
    _describe_invocation,
    _describe_invocations,
    _format_sections,
    _list_expected_commands,
    _masked_call_arguments,
    _numbered,
)
from .errors import UnexpectedCommandError, UnfulfilledExpectationError
from .test_doubles import DoubleKind

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from .controller import CommandDouble
    from .expectations import Expectation
    from .ipc import Invocation


def raise_rejected_mock_error(
    exp: Expectation, inv: Invocation, reason: str
) -> typ.NoReturn:
    """Raise for a mock call rejected at invocation time by fail-fast mode.

    Parameters
    ----------
    exp : Expectation
        The mock's expectation.
    inv : Invocation
        The rejected invocation.
    reason : str
        The stored, redacted mismatch reason.

    Raises
    ------
    UnexpectedCommandError
        Always, describing the expected and actual calls.
    """
    msg = _format_sections(
        "Unexpected command invocation.",
        [
            (
                "Expected",
                _describe_expectation(
                    exp, include_stdin=False, actual_calls=[inv.args]
                ),
            ),
            ("Actual", _describe_actual_call(exp, inv, include_stdin=False)),
            ("Reason", reason),
        ],
    )
    raise UnexpectedCommandError(msg)


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
                ("Expected", _describe_expectation(exp, actual_calls=[inv.args])),
                ("Actual", _describe_actual_call(exp, inv)),
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
                (
                    "Expected",
                    _describe_expectation(
                        exp, include_count=True, actual_calls=[inv.args]
                    ),
                ),
                ("Observed calls", str(observed)),
                ("Last call", _describe_actual_call(exp, inv)),
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
        self._current_masked_actuals: list[list[str]] = []

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
        # Journal position need not align with the expectation at that index,
        # so mask from the calls to each expectation's own command.
        expected_descriptions = [
            _describe_expectation(
                exp,
                actual_calls=self._mask_sources(exp, ordered_seq, relevant_invocations),
            )
            for exp in ordered_seq
        ]
        # A credential can hide under a different option in another call to the
        # same command, so each observed call is masked from all of them and
        # from the positions the counted expectations treat as sensitive.
        masked_actuals = _masked_call_arguments(relevant_invocations, ordered_seq)
        actual_descriptions = [
            _describe_invocation(inv, safe_args=safe)
            for inv, safe in zip(relevant_invocations, masked_actuals, strict=True)
        ]
        self._current_expected_descriptions = expected_descriptions
        self._current_actual_descriptions = actual_descriptions
        self._current_masked_actuals = masked_actuals

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
        raise UnfulfilledExpectationError(
            _order_diagnostic(
                "Ordered expectations not satisfied.",
                expected_descriptions,
                actual_descriptions,
                (
                    "Missing",
                    _numbered(expected_descriptions[index:], start=index + 1),
                ),
            )
        )

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

            self._raise_mismatch_error(
                index,
                exp,
                actual_inv,
                self._mask_sources(exp, ordered_seq, relevant_invocations),
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

        # Reuse the masked descriptions so an extra call cannot reintroduce a
        # credential that another call to the same command exposed.
        extras = actual_descriptions[len(ordered_seq) :]
        raise UnexpectedCommandError(
            _order_diagnostic(
                "Unexpected additional invocation.",
                expected_descriptions,
                actual_descriptions,
                ("Unexpected calls", "\n".join(extras)),
            )
        )

    @staticmethod
    def _mask_sources(
        exp: Expectation,
        ordered_seq: cabc.Sequence[Expectation],
        invocations: cabc.Sequence[Invocation],
    ) -> list[list[str]]:
        """Return the observed and expected arguments that mask *exp*.

        A sibling expectation can name the option that marks a bare literal as
        a credential, so its literal positions mask *exp* as well.

        Returns
        -------
        list[list[str]]
            Argument rows whose sensitive positions mask *exp*.
        """
        sources = [list(inv.args) for inv in invocations if inv.command == exp.name]
        for other in ordered_seq:
            if other is exp or other.name != exp.name:
                continue
            literals = other.args if other.args is not None else other.match_args
            sources.append([
                item if isinstance(item, str) else "" for item in literals or ()
            ])
        return sources

    def _raise_mismatch_error(
        self,
        index: int,
        exp: Expectation,
        actual_inv: Invocation,
        mask_calls: cabc.Sequence[cabc.Sequence[str]],
    ) -> None:
        """Raise a detailed error for the first out-of-order invocation."""
        reason = exp.explain_mismatch(actual_inv, mask_sources=mask_calls)
        mismatch = "\n".join([
            f"position {index + 1}",
            "expected:\n"
            + indent(
                _describe_expectation(exp, actual_calls=mask_calls),
                "  ",
            ),
            "actual:\n"
            + indent(
                _describe_actual_call(
                    exp, actual_inv, safe_args=self._current_masked_actuals[index]
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


def _order_diagnostic(
    title: str,
    expected_descriptions: list[str],
    actual_descriptions: list[str],
    tail: tuple[str, str],
) -> str:
    """Return an ordered-verification diagnostic ending with *tail*.

    Returns
    -------
    str
        Message with the numbered expected and observed orders.
    """
    return _format_sections(
        title,
        [
            ("Expected order", _numbered(expected_descriptions)),
            ("Observed order", _numbered(actual_descriptions)),
            tail,
        ],
    )


def _count_sections(exp: Expectation, calls: list[Invocation]) -> list[tuple[str, str]]:
    """Return the expected and observed-count sections for *exp*.

    Any recorded call shows which expected positions the command treats as
    credentials, so the expected rendering is masked against all of them.

    Returns
    -------
    list[tuple[str, str]]
        Labelled sections shared by both count diagnostics.
    """
    return [
        (
            "Expected",
            _describe_expectation(
                exp, include_count=True, actual_calls=[call.args for call in calls]
            ),
        ),
        ("Observed calls", f"{len(calls)} (expected {exp.count})"),
    ]


def _count_diagnostic(
    title: str,
    exp: Expectation,
    calls: list[Invocation],
    tail: tuple[str, str],
) -> str:
    """Return a count diagnostic ending with *tail*.

    Returns
    -------
    str
        Message with the shared count sections and the caller's final section.
    """
    return _format_sections(title, [*_count_sections(exp, calls), tail])


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
            focus_env = exp.env.keys()
            include_stdin = exp.stdin is not None
            if len(calls) < exp.count:
                raise UnfulfilledExpectationError(
                    _count_diagnostic(
                        "Unfulfilled expectation.",
                        exp,
                        calls,
                        (
                            "Recorded invocations",
                            _describe_invocations(
                                calls,
                                focus_env=focus_env,
                                include_stdin=include_stdin,
                                safe_args=_masked_call_arguments(calls, [exp]),
                            ),
                        ),
                    )
                )
            if len(calls) > exp.count:
                raise UnexpectedCommandError(
                    _count_diagnostic(
                        "Unexpected additional invocation.",
                        exp,
                        calls,
                        (
                            "Last call",
                            _describe_invocation(
                                calls[-1],
                                focus_env=focus_env,
                                include_stdin=include_stdin,
                                safe_args=_masked_call_arguments(calls, [exp])[-1],
                            ),
                        ),
                    )
                )
