"""Expectation matching helpers for command doubles."""

from __future__ import annotations

import dataclasses as dc
import re
import typing as typ

SENSITIVE_ENV_KEY_TOKENS: typ.Final[tuple[str, ...]] = (
    "secret",
    "token",
    "api_key",
    "password",
)
# Pre-normalize tokens once for case-insensitive checks
_SENSITIVE_TOKENS: typ.Final[tuple[str, ...]] = tuple(
    tok.casefold() for tok in SENSITIVE_ENV_KEY_TOKENS
)

# Comprehensive regex for secret-bearing env key segments.  Matches KEY,
# TOKEN, SECRET, PASSWORD, CREDENTIALS, PASS, and PWD as word segments
# delimited by underscores, hyphens, or string boundaries.
_SECRET_ENV_KEY_RE: typ.Final[re.Pattern[str]] = re.compile(
    r"(?i)(^|[_-])(KEY|TOKEN|SECRET|PASSWORD|CREDENTIALS?"
    r"|PASS(?:WORD)?|PWD)(?=[_-]|\d|$)"
)


def _is_sensitive_env_key(key: str) -> bool:
    """Return True if key likely holds secret material (substring match).

    Returns
    -------
    bool
        ``True`` when *key* contains one of the sensitive substrings.
    """
    k = key.casefold()
    return any(tkn in k for tkn in _SENSITIVE_TOKENS)


def is_sensitive_recording_env_key(key: str) -> bool:
    """Return True if *key* should be treated as secret-bearing for recordings.

    Combines the substring-based check from :func:`_is_sensitive_env_key` with
    a regex that catches word-segment patterns such as ``GITHUB_KEY`` or
    ``DB_PWD``.

    Returns
    -------
    bool
        Whether recordings should redact the environment variable's value.
    """
    return _is_sensitive_env_key(key) or bool(_SECRET_ENV_KEY_RE.search(key))


if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc

    from .ipc import Invocation


@dc.dataclass(slots=True)
class Expectation:
    """Expectation details for a command invocation."""

    name: str
    args: list[str] | None = None
    match_args: list[cabc.Callable[[str], bool]] | None = None
    stdin: str | cabc.Callable[[str], object] | None = None
    env: dict[str, str] = dc.field(default_factory=dict)
    count: int = 1
    ordered: bool = False

    def with_args(self, *args: str) -> Expectation:
        """Require ``args`` to match exactly.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.
        """
        self.args = list(args)
        return self

    def with_matching_args(self, *matchers: cabc.Callable[[str], bool]) -> Expectation:
        """Use callables in ``matchers`` to validate each argument.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.
        """
        self.match_args = list(matchers)
        return self

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

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.
        """
        self.count = count
        return self

    def times(self, count: int) -> Expectation:
        """Alias for :meth:`times_called` matching the fluent DSL.

        Returns
        -------
        Expectation
            This expectation, allowing further fluent configuration.
        """
        self.count = count
        return self

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
                if not matcher(arg):
                    return False
            except Exception:  # ruff: ignore[blind-except] - this boundary intentionally converts arbitrary callback failures
                return False
        return True

    def explain_mismatch(self, invocation: Invocation) -> str:
        """Return a reason why ``invocation`` failed to match.

        Returns
        -------
        str
            A human-readable explanation of the first mismatch found.
        """
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
        return "args, stdin, or env mismatch"

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

    def _explain_args_mismatch(self, invocation: Invocation) -> str | None:
        """Return a message if explicit args do not match.

        Returns
        -------
        str or None
            The mismatch description, or ``None`` when the arguments agree or
            no literal ``args`` were configured.
        """
        if self.args is None or invocation.args == self.args:
            return None
        return f"arguments {invocation.args!r} != {self.args!r}"

    def _explain_match_args_mismatch(self, invocation: Invocation) -> str | None:
        """Return a message when matcher-based args fail.

        Returns
        -------
        str or None
            The first arity, rejection, or validator-error description, or
            ``None`` when every matcher accepts its argument.
        """
        if self.match_args is None:
            return None
        if len(invocation.args) != len(self.match_args):
            return (
                f"expected {len(self.match_args)} args but got {len(invocation.args)}"
            )
        for i, (arg, matcher) in enumerate(
            zip(invocation.args, self.match_args, strict=True),
        ):
            try:
                ok = bool(matcher(arg))
            except Exception as exc:  # ruff: ignore[blind-except] - this boundary intentionally converts arbitrary callback failures
                return (
                    f"arg[{i}] predicate {matcher!r} raised "
                    f"{exc.__class__.__name__}: {exc}"
                )
            if not ok:
                return f"arg[{i}]={arg!r} failed {matcher!r}"
        return None

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
