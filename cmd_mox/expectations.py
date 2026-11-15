"""Expectation matching helpers for command doubles."""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc
import typing as typ

from ._argument_redaction import (
    is_sensitive_recording_env_key,
)
from ._argument_redaction import (
    mask_sensitive_positions as _mask_sensitive_positions,
)
from ._argument_redaction import (
    redact_sensitive_argument_values as _redact_sensitive_argument_values,
)
from ._matching import (
    MismatchContext as _MismatchContext,
)
from ._matching import (
    matcher_accepts as _matcher_accepts,
)
from ._matching import (
    matcher_mismatch as _matcher_mismatch,
)
from ._matching import (
    redacted_literal_argument_mismatch as _redacted_literal_argument_mismatch,
)
from ._validators import validate_expectation_count
from .errors import ExpectationConfigurationError

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from .ipc import Invocation

# A literal string matches an argument exactly; any other matcher must be a
# callable predicate whose result is coerced to ``bool``.
ArgMatcher = str | cabc.Callable[[str], bool]


@dc.dataclass(slots=True)
class Expectation:
    """Expectation details for a command invocation."""

    name: str
    args: list[str] | None = None
    match_args: list[ArgMatcher] | None = None
    stdin: str | cabc.Callable[[str], object] | None = None
    env: dict[str, str] = dc.field(default_factory=dict)
    count: int = 1
    ordered: bool = False
    # Only the fluent count setters set this flag. Verification distinguishes an
    # explicit ``times(1)`` from the implicit default so that spies stay
    # observational unless the caller asks for a count. It is excluded from
    # equality because ordering membership compares expectations, and from the
    # repr because it is an implementation detail.
    count_explicit: bool = dc.field(default=False, compare=False, repr=False)

    def with_args(self, *args: str) -> Expectation:
        """Require ``args`` to match exactly.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.

        Raises
        ------
        ExpectationConfigurationError
            If an argument expectation is already configured.
        """
        configured_args = list(args)
        if self.args is not None or self.match_args is not None:
            safe_configured_args = _redact_sensitive_argument_values(
                configured_args, command=self.name
            )
            attempted = f"args={safe_configured_args!r}"
            error_message = self._argument_expectation_conflict(attempted)
            if error_message is not None:
                raise ExpectationConfigurationError(error_message)
        self.args = configured_args
        return self

    def with_matching_args(self, *matchers: ArgMatcher) -> Expectation:
        """Match each argument with a literal string or callable predicate.

        A ``str`` matcher requires exact equality with the corresponding
        argument. Any other matcher must be callable and is evaluated with the
        argument; its result is coerced to ``bool``.

        Parameters
        ----------
        *matchers : str or collections.abc.Callable
            One matcher per positional argument. Strings match exactly;
            callables act as predicates.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.

        Raises
        ------
        ExpectationConfigurationError
            If an argument expectation is already configured, or a matcher is
            neither a string nor callable.
        """
        configured_matchers = list(matchers)
        matcher_types = [type(matcher).__name__ for matcher in configured_matchers]
        error_message = self._argument_expectation_conflict(
            f"match_args={matcher_types!r}"
        )
        if error_message is not None:
            raise ExpectationConfigurationError(error_message)
        for position, matcher in enumerate(configured_matchers):
            if isinstance(matcher, str) or callable(matcher):
                continue
            msg = (
                f"Command {self.name!r} matcher at position {position} must be "
                f"a str or callable, got {type(matcher).__name__}. Use a "
                "literal string for exact equality or a callable predicate "
                "such as cmd_mox.Any()."
            )
            raise ExpectationConfigurationError(msg)
        self.match_args = configured_matchers
        return self

    def _argument_expectation_conflict(self, attempted: str) -> str | None:
        """Return a conflict message, if an argument expectation is set.

        Returns
        -------
        str or None
            Conflict message, if one exists.
        """
        if self.args is None and self.match_args is None:
            return None

        if self.args is not None:
            safe_args = _redact_sensitive_argument_values(self.args, command=self.name)
            existing = f"args={safe_args!r}"
        else:
            matcher_types = [
                type(matcher).__name__ for matcher in self.match_args or []
            ]
            existing = f"match_args={matcher_types!r}"
        return (
            f"Command {self.name!r} already has {existing}; cannot set "
            f"{attempted}. Use `cmd_mox.spy({self.name!r}).runs(handler)` "
            "for multiple invocations of one command."
        )

    def with_stdin(self, data: str | cabc.Callable[[str], object]) -> Expectation:
        """Expect ``stdin`` to equal ``data`` or satisfy a predicate.

        The predicate's return value will be coerced to bool.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.
        """
        self.stdin = data
        return self

    def with_env(self, mapping: dict[str, str]) -> Expectation:
        """Require environment variables in ``mapping``.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.

        Raises
        ------
        TypeError
            If a key or value is not a string.
        ValueError
            If a key is empty.
        """
        for key, value in mapping.items():
            if not isinstance(key, str):
                msg = f"Environment variable name must be str, got {type(key).__name__}"
                raise TypeError(msg)
            if not key:
                msg = "Environment variable name cannot be empty"
                raise ValueError(msg)
            if not isinstance(value, str):
                msg = (
                    "Environment variable value must be str, "
                    f"got {type(value).__name__} for {key!r}"
                )
                raise TypeError(msg)
        self.env = mapping.copy()
        return self

    def times_called(self, count: int) -> Expectation:
        """Set the required invocation count to ``count``.

        Only this fluent setter (and :meth:`times`, which delegates to it) marks
        the count as explicit. Verification enforces explicit counts on mocks
        and spies, while an unconfigured spy remains observational.

        Parameters
        ----------
        count : int
            Required number of invocations. Must be a non-negative integer;
            a zero count requires the command not to be invoked at all.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.

        Raises
        ------
        TypeError
            If *count* is a bool or not an integer.
        ValueError
            If *count* is negative.
        """  # ruff: ignore[docstring-extraneous-exception] - validate_expectation_count raises these caller-visible failures
        validate_expectation_count(count)
        self.count = count
        self.count_explicit = True
        return self

    # Deliberate public API: `times` is the documented fluent DSL verb. Its
    # body forwards to `times_called` because the two identical count-setter
    # bodies were what nose reported as a clone family; one implementation
    # cannot drift from the other.
    # pylint: disable-next=trivial-attribute-wrapper
    def times(self, count: int) -> Expectation:
        """Alias for :meth:`times_called` matching the fluent DSL.

        Like :meth:`times_called`, this marks the count as explicit so that
        verification enforces it for mocks and spies. Assigning ``count``
        directly does not set the flag, which keeps an unconfigured spy
        observational. See :meth:`times_called` for the accepted values and
        the errors raised for an invalid count.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.
        """
        return self.times_called(count)

    def in_order(self) -> Expectation:
        """Mark this expectation as ordered relative to others.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.
        """
        self.ordered = True
        return self

    def any_order(self) -> Expectation:
        """Allow this expectation to occur in any order.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.
        """
        self.ordered = False
        return self

    def matches(self, invocation: Invocation) -> bool:
        """Return ``True`` if *invocation* satisfies this expectation.

        Returns
        -------
        bool
            Whether command, arguments, stdin, and environment all match.
        """
        return (
            self._matches_command(invocation)
            and self._matches_args(invocation)
            and self._matches_stdin(invocation)
            and self._matches_env(invocation)
        )

    def _matches_command(self, invocation: Invocation) -> bool:
        """Return ``True`` if the command name matches.

        Returns
        -------
        bool
            ``True`` when the invoked command equals the expected name.
        """
        return invocation.command == self.name

    def _matches_args(self, invocation: Invocation) -> bool:
        """Validate positional arguments.

        Returns
        -------
        bool
            ``True`` when the invocation satisfies the literal ``args`` and any
            ``match_args`` validators.
        """
        if self.args is not None and invocation.args != self.args:
            return False
        if self.match_args is not None:
            return self._validate_matchers(invocation.args)
        return True

    def _validate_matchers(self, args: list[str]) -> bool:
        """Return ``True`` if ``args`` satisfy ``match_args`` validators.

        Returns
        -------
        bool
            ``True`` when arity matches and every validator accepts its
            argument without raising.
        """
        matchers = self.match_args
        if matchers is None:
            # Defensive fallback: callers ensure ``match_args`` is set before
            # invoking this helper, but default to ``False`` rather than
            # raising so verification produces a clean mismatch message.
            return False
        if len(args) != len(matchers):
            return False
        for arg, matcher in zip(args, matchers, strict=True):
            try:
                if not _matcher_accepts(matcher, arg):
                    return False
            except Exception:  # ruff: ignore[blind-except] - this boundary intentionally converts arbitrary callback failures
                return False
        return True

    def definite_mismatch_reason(self, invocation: Invocation) -> str | None:
        """Return a reason when *invocation* definitely cannot match.

        Only checks that need no callback are considered: literal argument
        inequality, matcher arity, unequal literal matchers, and literal stdin
        inequality. Predicate matchers, environment constraints, and counts
        stay with verification, so a call that might still match is never
        rejected early.

        Rendered arguments are redacted, and stdin content is never echoed
        because either may carry a credential.

        Returns
        -------
        str or None
            A redacted mismatch description, or ``None`` when the invocation
            is not definitely mismatched.
        """
        literal = self._literal_args_mismatch(invocation)
        if literal is not None:
            return literal
        if self.match_args is not None:
            mismatch = _matcher_mismatch(
                self,
                invocation,
                self.match_args,
                context=_MismatchContext(call_predicates=False),
            )
            if mismatch is not None:
                return mismatch
        if isinstance(self.stdin, str) and invocation.stdin != self.stdin:
            # Never echo stdin: it may carry a credential or another secret.
            return "stdin does not match the expected value"
        return None

    def redacted_arguments(self, args: cabc.Sequence[str]) -> list[str]:
        """Return *args* with every credential position masked.

        The call's own option names identify most secrets, but the option name
        may differ from the expected one, so the expectation's sensitive
        positions are applied as well.

        Parameters
        ----------
        args : collections.abc.Sequence[str]
            Arguments of the invocation being reported.

        Returns
        -------
        list[str]
            Diagnostic-safe arguments with positions preserved.
        """
        safe = _redact_sensitive_argument_values(list(args), command=self.name)
        expected = self.args if self.args is not None else self.match_args
        if expected is None:
            return safe
        literals = [item if isinstance(item, str) else "" for item in expected]
        return _mask_sensitive_positions(safe, literals, command=self.name)

    def explain_mismatch(
        self,
        invocation: Invocation,
        *,
        mask_sources: cabc.Sequence[cabc.Sequence[str]] | None = None,
    ) -> str:
        """Return a reason why ``invocation`` failed to match.

        Parameters
        ----------
        invocation : Invocation
            Call that failed to match.
        mask_sources : collections.abc.Sequence[collections.abc.Sequence[str]], optional
            Sibling argument rows whose sensitive positions must be masked in
            the reason, such as the other ordered calls and expectations.

        Returns
        -------
        str
            A human-readable explanation of the first mismatch found.
        """
        reason = self._explain_command_mismatch(invocation)
        if reason:
            return reason
        reason = self._literal_args_mismatch(invocation, mask_sources)
        if reason:
            return reason
        reason = self._explain_match_args_mismatch(invocation, mask_sources)
        if reason:
            return reason
        reason = self._explain_stdin_mismatch(invocation)
        if reason:
            return reason
        return self._explain_env_mismatch(invocation) or "args, stdin, or env mismatch"

    def _explain_command_mismatch(self, invocation: Invocation) -> str | None:
        """Return a message if the command name differs.

        Returns
        -------
        str or None
            The mismatch description, or ``None`` when the names agree.
        """
        if self._matches_command(invocation):
            return None
        return f"command {invocation.command!r} != {self.name!r}"

    def _literal_args_mismatch(
        self,
        invocation: Invocation,
        mask_sources: cabc.Sequence[cabc.Sequence[str]] | None = None,
    ) -> str | None:
        """Return the literal-argument mismatch description, if any.

        Returns
        -------
        str or None
            The redacted mismatch, or ``None`` when the arguments agree or no
            literal ``args`` were configured.
        """
        if self.args is None or invocation.args == self.args:
            return None
        return _redacted_literal_argument_mismatch(
            self, invocation, self.args, mask_sources
        )

    def _explain_match_args_mismatch(
        self,
        invocation: Invocation,
        mask_sources: cabc.Sequence[cabc.Sequence[str]] | None = None,
    ) -> str | None:
        """Return a message when matcher-based args fail.

        Returns
        -------
        str or None
            The first arity, rejection, or validator-error description, or
            ``None`` when every matcher accepts its argument.
        """
        if self.match_args is None:
            return None
        return _matcher_mismatch(
            self,
            invocation,
            self.match_args,
            context=_MismatchContext(mask_sources=mask_sources),
        )

    def _explain_stdin_mismatch(self, invocation: Invocation) -> str | None:
        """Return a message if stdin fails to satisfy the expectation.

        Returns
        -------
        str or None
            The mismatch description, or ``None`` when stdin is unconstrained
            or satisfies the expectation.
        """
        stdin = self.stdin
        if stdin is None:
            return None
        if isinstance(stdin, str):
            return self._explain_stdin_string_mismatch(invocation, stdin)
        if not callable(stdin):
            return f"stdin expectation {stdin!r} is not str or callable"
        return self._explain_stdin_predicate_mismatch(invocation, stdin)

    @staticmethod
    def _explain_stdin_string_mismatch(
        invocation: Invocation, stdin: str
    ) -> str | None:
        """Return a message if literal stdin fails to match the expectation.

        Returns
        -------
        str or None
            The mismatch description, or ``None`` when the strings are equal.
        """
        if invocation.stdin != stdin:
            return f"stdin {invocation.stdin!r} != {stdin!r}"
        return None

    @staticmethod
    def _explain_stdin_predicate_mismatch(
        invocation: Invocation, stdin: cabc.Callable[[str], object]
    ) -> str | None:
        """Return a message if the stdin predicate rejects or raises.

        Returns
        -------
        str or None
            The rejection or predicate-error description, or ``None`` when the
            predicate accepts the recorded stdin.
        """
        try:
            ok = bool(stdin(invocation.stdin))
        except Exception as exc:  # ruff: ignore[blind-except] - this boundary intentionally converts arbitrary callback failures
            return f"stdin predicate {stdin!r} raised {exc.__class__.__name__}: {exc}"
        if not ok:
            return f"stdin {invocation.stdin!r} failed {stdin!r}"
        return None

    def _explain_env_mismatch(self, invocation: Invocation) -> str | None:
        """Return a message if an env variable mismatch is found.

        Returns
        -------
        str or None
            The first mismatch description with secret values redacted, or
            ``None`` when every expected variable matches.
        """
        if not self.env:
            return None
        for key, value in self.env.items():
            actual = invocation.env.get(key)
            if actual != value:
                sensitive = is_sensitive_recording_env_key(key)
                exp = "***" if sensitive else value
                act = "***" if actual is not None and sensitive else actual
                return f"env[{key!r}]={act!r} != {exp!r}"
        return None

    def _matches_stdin(self, invocation: Invocation) -> bool:
        """Check stdin data or predicate.

        Returns
        -------
        bool
            ``True`` when stdin is unconstrained, equals the expected literal,
            or is accepted by the configured predicate.
        """
        if self.stdin is None:
            return True
        if isinstance(self.stdin, str):
            return invocation.stdin == self.stdin
        if callable(self.stdin):
            try:
                return bool(self.stdin(invocation.stdin))
            except Exception:  # ruff: ignore[blind-except] - this boundary intentionally converts arbitrary callback failures
                return False
        return False

    def _matches_env(self, invocation: Invocation) -> bool:
        """Verify required environment variables.

        Returns
        -------
        bool
            ``True`` when every expected environment variable matches.
        """
        return all(invocation.env.get(key) == value for key, value in self.env.items())
