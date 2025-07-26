"""Verification helpers for :class:`CmdMox`."""

from __future__ import annotations

import typing as t

from .errors import UnexpectedCommandError, UnfulfilledExpectationError

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from .controller import CommandDouble
    from .expectations import Expectation
    from .ipc import Invocation


class UnexpectedCommandVerifier:
    """Check invocations match registered expectations."""

    def verify(
        self,
        journal: t.Iterable[Invocation],
        doubles: t.Mapping[str, CommandDouble],
    ) -> None:
        """Raise if *journal* contains calls not matching registered doubles."""
        for inv in journal:
            dbl = doubles.get(inv.command)
            if dbl is None:
                msg = f"Unexpected commands invoked: {inv.command}"
                raise UnexpectedCommandError(msg)
            if dbl.kind != "stub" and not dbl.expectation.matches(inv):
                msg = f"Unexpected invocation for {inv.command}: args or stdin mismatch"
                raise UnexpectedCommandError(msg)


class OrderVerifier:
    """Validate ordering of expectations marked with ``in_order``."""

    def __init__(self, ordered: list[Expectation]) -> None:
        self._ordered = ordered

    def verify(self, journal: t.Iterable[Invocation]) -> None:
        """Ensure ordered expectations appear in order within *journal*."""
        ordered_seq: list[Expectation] = []
        for exp in self._ordered:
            ordered_seq.extend([exp] * exp.times)
        index = 0
        for inv in journal:
            if index >= len(ordered_seq):
                break
            exp = ordered_seq[index]
            if exp.matches(inv):
                index += 1
        if index != len(ordered_seq):
            remaining = [e.name for e in ordered_seq[index:]]
            msg = f"Expected commands not called in order: {remaining}"
            raise UnfulfilledExpectationError(msg)


class CountVerifier:
    """Check that each expectation was met the expected number of times."""

    def verify(
        self,
        expectations: t.Mapping[str, Expectation],
        invocations: t.Mapping[str, list[Invocation]],
    ) -> None:
        """Validate invocation counts against ``expectations``."""
        for name, exp in expectations.items():
            expected = exp.times
            actual = len(invocations.get(name, []))
            if actual < expected:
                msg = f"Expected {name} to be called {expected} times but got {actual}"
                raise UnfulfilledExpectationError(msg)
            if actual > expected:
                msg = f"{name} called more than expected ({actual} > {expected})"
                raise UnexpectedCommandError(msg)
