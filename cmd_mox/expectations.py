"""Expectation matching helpers for command doubles."""

from __future__ import annotations

import dataclasses as dc
import typing as t

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from .ipc import Invocation


@dc.dataclass(slots=True)
class Expectation:
    """Expectation details for a command invocation."""

    name: str
    args: list[str] | None = None
    match_args: list[t.Callable[[str], bool]] | None = None
    stdin: str | t.Callable[[str], bool] | None = None
    env: dict[str, str] = dc.field(default_factory=dict)
    times: int = 1
    ordered: bool = False

    def with_args(self, *args: str) -> Expectation:
        """Require ``args`` to match exactly."""
        self.args = list(args)
        return self

    def with_matching_args(self, *matchers: t.Callable[[str], bool]) -> Expectation:
        """Use callables in ``matchers`` to validate each argument."""
        self.match_args = list(matchers)
        return self

    def with_stdin(self, data: str | t.Callable[[str], bool]) -> Expectation:
        """Expect ``stdin`` to equal ``data`` or satisfy a predicate."""
        self.stdin = data
        return self

    def with_env(self, mapping: dict[str, str]) -> Expectation:
        """Require environment variables in ``mapping``."""
        self.env = mapping.copy()
        return self

    def times_called(self, count: int) -> Expectation:
        """Set the required invocation count to ``count``."""
        self.times = count
        return self

    def in_order(self) -> Expectation:
        """Mark this expectation as ordered relative to others."""
        self.ordered = True
        return self

    def any_order(self) -> Expectation:
        """Allow this expectation to occur in any order."""
        self.ordered = False
        return self

    def matches(self, invocation: Invocation) -> bool:
        """Return ``True`` if *invocation* satisfies this expectation."""
        if invocation.command != self.name:
            return False
        if self.args is not None and invocation.args != self.args:
            return False
        if self.match_args is not None:
            if len(invocation.args) != len(self.match_args):
                return False
            for arg, matcher in zip(invocation.args, self.match_args, strict=True):
                if not matcher(arg):
                    return False
        if self.stdin is not None:
            if callable(self.stdin):
                if not self.stdin(invocation.stdin):
                    return False
            elif invocation.stdin != self.stdin:
                return False
        return all(invocation.env.get(key) == value for key, value in self.env.items())
