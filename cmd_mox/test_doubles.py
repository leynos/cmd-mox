"""Command double implementations and expectation proxies."""

from __future__ import annotations

import typing as t

from typing_extensions import Self

from .expectations import Expectation
from .ipc import Invocation, Response

if t.TYPE_CHECKING:  # pragma: no cover - typing-only import
    from .controller import CmdMox

T = t.TypeVar("T")


def _create_expectation_proxy() -> type:
    """Return a proxy type for expectation delegation.

    Static type checking requires a protocol so ``CommandDouble`` exposes the
    full expectation interface.  At runtime we return a minimal placeholder
    whose methods raise ``NotImplementedError`` if accessed directly, making
    this typing-only pattern explicit.
    """
    if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
        from pathlib import Path  # noqa: F401

        class _ExpectationProxy(t.Protocol):
            def with_args(self, *args: str) -> Self: ...

            def with_matching_args(
                self, *matchers: t.Callable[[str], bool]
            ) -> Self: ...

            def with_stdin(self, data: str | t.Callable[[str], bool]) -> Self: ...

            def with_env(self, mapping: dict[str, str]) -> Self: ...

            def times(self, count: int) -> Self: ...

            def times_called(self, count: int) -> Self: ...

            def in_order(self) -> Self: ...

            def any_order(self) -> Self: ...

        return _ExpectationProxy

    class _ExpectationProxy:  # pragma: no cover - runtime placeholder
        def __getattr__(self, name: str) -> t.Callable[..., t.NoReturn]:
            """Raise NotImplementedError for any method access."""

            def _method(*args: object, **kwargs: object) -> t.NoReturn:
                raise NotImplementedError(f"{name} is typing-only")

            return _method

    return _ExpectationProxy


_ExpectationProxy = _create_expectation_proxy()


T_Kind: t.TypeAlias = t.Literal["stub", "mock", "spy"]


class CommandDouble(_ExpectationProxy):  # type: ignore[misc]  # runtime proxy; satisfies typing-only protocol
    """Configuration for a stub, mock, or spy command."""

    __slots__ = (
        "controller",
        "expectation",
        "handler",
        "invocations",
        "kind",
        "name",
        "passthrough_mode",
        "response",
    )

    def __init__(self, name: str, controller: CmdMox, kind: T_Kind) -> None:
        self.name = name
        self.kind = kind
        self.controller = controller  # CmdMox instance
        self.response = Response()
        self.handler: t.Callable[[Invocation], Response] | None = None
        self.invocations: list[Invocation] = []
        self.passthrough_mode = False
        self.expectation = Expectation(name)

    def returns(self, stdout: str = "", stderr: str = "", exit_code: int = 0) -> Self:
        """Set the static response and return ``self``."""
        self.response = Response(stdout=stdout, stderr=stderr, exit_code=exit_code)
        self.handler = None
        return self

    def runs(
        self,
        handler: t.Callable[[Invocation], tuple[str, str, int] | Response],
    ) -> Self:
        """Use *handler* to generate responses dynamically."""

        def _wrap(invocation: Invocation) -> Response:
            result = handler(invocation)
            if isinstance(result, Response):
                return result
            match result:
                case (str() as stdout, str() as stderr, int() as exit_code):
                    return Response(stdout=stdout, stderr=stderr, exit_code=exit_code)
                case _:
                    msg = (
                        "Handler result must be a tuple of (str, str, int), "
                        f"got {type(result)}: {result}"
                    )
                    raise TypeError(msg)

        self.handler = _wrap
        return self

    # ------------------------------------------------------------------
    # Expectation configuration via delegation
    # ------------------------------------------------------------------
    def _ensure_in_order(self) -> None:
        """Register this expectation for ordered verification."""
        if self.expectation not in self.controller._ordered:
            self.controller._ordered.append(self.expectation)

    def _ensure_any_order(self) -> None:
        """Remove this expectation from ordered verification."""
        if self.expectation in self.controller._ordered:
            self.controller._ordered.remove(self.expectation)

    def with_args(self, *args: str) -> Self:
        """Require the command be invoked with *args*."""
        self.expectation.with_args(*args)
        return self

    def with_matching_args(self, *matchers: t.Callable[[str], bool]) -> Self:
        """Validate arguments using matcher predicates."""
        self.expectation.with_matching_args(*matchers)
        return self

    def with_stdin(self, data: str | t.Callable[[str], bool]) -> Self:
        """Expect the given stdin ``data`` or matcher."""
        self.expectation.with_stdin(data)
        return self

    def with_env(self, mapping: dict[str, str]) -> Self:
        """Expect the provided environment mapping."""
        self.expectation.with_env(mapping)
        return self

    def times(self, count: int) -> Self:
        """Require the command be invoked exactly ``count`` times."""
        self.expectation.times(count)
        return self

    def times_called(self, count: int) -> Self:
        """Verify the spy was called ``count`` times."""
        self.expectation.times_called(count)
        return self

    def in_order(self) -> Self:
        """Mark this expectation as ordered."""
        self.expectation.in_order()
        self._ensure_in_order()
        return self

    def any_order(self) -> Self:
        """Mark this expectation as unordered."""
        self.expectation.any_order()
        self._ensure_any_order()
        return self

    def passthrough(self) -> Self:
        """Execute the real command while recording invocations."""
        if self.kind != "spy":
            msg = "passthrough() is only valid for spies"
            raise ValueError(msg)
        self.passthrough_mode = True
        return self

    # ------------------------------------------------------------------
    # Matching helpers
    # ------------------------------------------------------------------
    def matches(self, invocation: Invocation) -> bool:
        """Return ``True`` if *invocation* satisfies the expectation."""
        return self.expectation.matches(invocation)

    @property
    def is_expected(self) -> bool:
        """Return ``True`` only for mocks."""
        return self.kind == "mock"

    @property
    def is_recording(self) -> bool:
        """Return ``True`` for mocks and spies."""
        return self.kind in ("mock", "spy")

    @property
    def call_count(self) -> int:
        """Return the number of recorded invocations."""
        return len(self.invocations)

    # ------------------------------------------------------------------
    # Spy assertions
    # ------------------------------------------------------------------
    def assert_called(self) -> None:
        """Raise ``AssertionError`` if this spy was never invoked."""
        self._validate_spy_usage("assert_called")
        if not self.invocations:
            msg = (
                f"Expected {self.name!r} to be called at least once but it was"
                " never called"
            )
            raise AssertionError(msg)

    def assert_not_called(self) -> None:
        """Raise ``AssertionError`` if this spy was invoked."""
        self._validate_spy_usage("assert_not_called")
        if self.invocations:
            last = self.invocations[-1]
            msg = (
                f"Expected {self.name!r} to be uncalled but it was called"
                f" {len(self.invocations)} time(s); "
                f"last args={last.args!r}, stdin={last.stdin!r}, env={last.env!r}"
            )
            raise AssertionError(msg)

    def assert_called_with(
        self,
        *args: str,
        stdin: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Assert the most recent call used the given arguments and context."""
        self._validate_spy_usage("assert_called_with")
        invocation = self._get_last_invocation()
        self._validate_arguments(invocation, args)
        self._validate_stdin(invocation, stdin)
        self._validate_environment(invocation, env)

    # ------------------------------------------------------------------
    # Spy assertion helpers
    # ------------------------------------------------------------------
    def _validate_spy_usage(self, method_name: str) -> None:
        if self.kind != "spy":  # pragma: no cover - defensive guard
            msg = f"{method_name}() is only valid for spies"
            raise AssertionError(msg)

    def _get_last_invocation(self) -> Invocation:
        if not self.invocations:
            msg = f"Expected {self.name!r} to be called but it was never called"
            raise AssertionError(msg)
        return self.invocations[-1]

    def _assert_equal(self, label: str, actual: T, expected: T) -> None:
        """Raise ``AssertionError`` if *actual* != *expected*.

        The *label* provides contextual information for the error message,
        yielding a consistent formatting across different validations.
        """
        if actual != expected:
            msg = f"{self.name!r} called with {label} {actual!r}, expected {expected!r}"
            raise AssertionError(msg)

    def _validate_arguments(
        self, invocation: Invocation, expected_args: tuple[str, ...]
    ) -> None:
        self._assert_equal("args", tuple(invocation.args), expected_args)

    def _validate_stdin(
        self, invocation: Invocation, expected_stdin: str | None
    ) -> None:
        if expected_stdin is not None:
            self._assert_equal("stdin", invocation.stdin, expected_stdin)

    def _validate_environment(
        self, invocation: Invocation, expected_env: dict[str, str] | None
    ) -> None:
        if expected_env is not None:
            self._assert_equal("env", invocation.env, expected_env)

    def __repr__(self) -> str:
        """Return debugging representation with name, kind, and response."""
        return (
            f"CommandDouble(name={self.name!r}, "
            f"kind={self.kind!r}, "
            f"response={self.response!r})"
        )

    __str__ = __repr__


# Backwards compatibility aliases
StubCommand = CommandDouble
MockCommand = CommandDouble
SpyCommand = CommandDouble
