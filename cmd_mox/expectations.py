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
    count: int = 1
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
        self.count = count
        return self

    def times(self, count: int) -> Expectation:
        """Alias for :meth:`times_called` matching the fluent DSL."""
        return self.times_called(count)

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
        return (
            self._matches_command(invocation)
            and self._matches_args(invocation)
            and self._matches_stdin(invocation)
            and self._matches_env(invocation)
        )

    def _matches_command(self, invocation: Invocation) -> bool:
        """Return ``True`` if the command name matches."""
        return invocation.command == self.name

    def _matches_args(self, invocation: Invocation) -> bool:
        """Validate positional arguments."""
        if self.args is not None and invocation.args != self.args:
            return False
        if self.match_args is not None:
            return self._validate_matchers(invocation.args)
        return True

    def _validate_matchers(self, args: list[str]) -> bool:
        """Return ``True`` if ``args`` satisfy ``match_args`` validators."""
        if len(args) != len(self.match_args):
            return False
        for arg, matcher in zip(args, self.match_args, strict=True):
            if not matcher(arg):
                return False
        return True

    def explain_mismatch(self, invocation: Invocation) -> str:
        """Return a reason why ``invocation`` failed to match."""
        for checker in (
            self._explain_command_mismatch,
            self._explain_args_mismatch,
            self._explain_match_args_mismatch,
            self._explain_stdin_mismatch,
            self._explain_env_mismatch,
        ):
            reason = checker(invocation)
            if reason:
                return reason
        return "args or stdin mismatch"

    def _explain_command_mismatch(self, invocation: Invocation) -> str | None:
        """Return a message if the command name differs."""
        if self._matches_command(invocation):
            return None
        return f"command {invocation.command!r} != {self.name!r}"

    def _explain_args_mismatch(self, invocation: Invocation) -> str | None:
        """Return a message if explicit args do not match."""
        if self.args is None or invocation.args == self.args:
            return None
        return f"arguments {invocation.args!r} != {self.args!r}"

    def _explain_match_args_mismatch(self, invocation: Invocation) -> str | None:
        """Return a message when matcher-based args fail."""
        if self.match_args is None:
            return None
        if len(invocation.args) != len(self.match_args):
            return (
                f"expected {len(self.match_args)} args but got {len(invocation.args)}"
            )
        for i, (arg, matcher) in enumerate(
            zip(invocation.args, self.match_args, strict=True)
        ):
            if not matcher(arg):
                return f"arg[{i}]={arg!r} failed {matcher!r}"
        return None

    def _explain_stdin_mismatch(self, invocation: Invocation) -> str | None:
        """Return a message if stdin fails to satisfy the expectation."""
        if self.stdin is None:
            return None
        if callable(self.stdin):
            try:
                ok = bool(self.stdin(invocation.stdin))
            except Exception as exc:  # noqa: BLE001
                return (
                    f"stdin predicate {self.stdin!r} raised "
                    f"{exc.__class__.__name__}: {exc}"
                )
            if not ok:
                return f"stdin {invocation.stdin!r} failed {self.stdin!r}"
        elif invocation.stdin != self.stdin:
            return f"stdin {invocation.stdin!r} != {self.stdin!r}"
        return None

    def _explain_env_mismatch(self, invocation: Invocation) -> str | None:
        """Return a message if an env variable mismatch is found."""
        if not self.env:
            return None
        for key, value in self.env.items():
            actual = invocation.env.get(key)
            if actual != value:

                def _is_sensitive(k: str) -> bool:
                    k = k.lower()
                    return any(s in k for s in ("secret", "token", "key", "password"))

                exp = "***" if _is_sensitive(key) else value
                act = "***" if (actual is not None and _is_sensitive(key)) else actual
                return f"env[{key!r}]={act!r} != {exp!r}"
        return None

    def _matches_stdin(self, invocation: Invocation) -> bool:
        """Check stdin data or predicate."""
        if self.stdin is None:
            return True
        if callable(self.stdin):
            return bool(self.stdin(invocation.stdin))
        return invocation.stdin == self.stdin

    def _matches_env(self, invocation: Invocation) -> bool:
        """Verify required environment variables."""
        return all(invocation.env.get(key) == value for key, value in self.env.items())
