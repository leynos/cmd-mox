"""Command double implementations and expectation proxies."""

from __future__ import annotations

import enum
import typing as typ

from .expectations import Expectation
from .ipc import Invocation, Response

if typ.TYPE_CHECKING:  # pragma: no cover - typing-only import
    import collections.abc as cabc

    from .controller import CmdMox

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    from pathlib import Path

    from .record.replay import ReplaySession
    from .record.scrubber import Scrubber
    from .record.session import RecordingSession

    class _ExpectationProtocol(typ.Protocol):
        def with_args(self, *args: str) -> typ.Self: ...

        def with_matching_args(
            self, *matchers: cabc.Callable[[str], bool]
        ) -> typ.Self: ...

        def with_stdin(self, data: str | cabc.Callable[[str], bool]) -> typ.Self: ...

        def with_env(self, mapping: dict[str, str]) -> typ.Self: ...

        def times(self, count: int) -> typ.Self: ...

        def times_called(self, count: int) -> typ.Self: ...

        def in_order(self) -> typ.Self: ...

        def any_order(self) -> typ.Self: ...

    _ExpectationType = _ExpectationProtocol
else:

    class _ExpectationType:  # pragma: no cover - runtime placeholder
        def __getattr__(self, name: str) -> cabc.Callable[..., typ.NoReturn]:
            """Return a callable that rejects typing-only method access.

            Returns
            -------
            collections.abc.Callable
                Callable that raises ``NotImplementedError`` when invoked.
            """

            def _method(*args: object, **kwargs: object) -> typ.NoReturn:
                raise NotImplementedError(f"{name} is typing-only")

            return _method


def _create_expectation_proxy() -> type:
    """Return the active expectation proxy type.

    Typing builds see a :class:`typing.Protocol`; runtime receives a lightweight
    class that raises when accessed directly.

    Returns
    -------
    type
        The lightweight runtime expectation proxy type.
    """
    return _ExpectationType


_ExpectationProxy = _create_expectation_proxy()


class DoubleKind(enum.StrEnum):
    """Kinds of command doubles supported by :class:`CommandDouble`."""

    STUB = "stub"
    MOCK = "mock"
    SPY = "spy"


# ruff: ignore[too-many-public-methods] - deliberately wide fluent stub/mock/spy
# builder API; splitting it would fragment a single cohesive DSL.
class CommandDouble(_ExpectationProxy):  # type: ignore[misc, ty:unsupported-base]  # runtime proxy; satisfies typing-only protocol
    """Configuration for a stub, mock, or spy command."""

    __slots__ = (
        "_recording_session",
        "_replay_session",
        "controller",
        "expectation",
        "handler",
        "invocations",
        "kind",
        "name",
        "passthrough_mode",
        "response",
    )

    def __init__(self, name: str, controller: CmdMox, kind: DoubleKind) -> None:
        self.name = name
        self.kind: DoubleKind = kind
        self.controller = controller  # CmdMox instance
        self.response = Response()
        self.handler: cabc.Callable[[Invocation], Response] | None = None
        self.invocations: list[Invocation] = []
        self.passthrough_mode = False
        self.expectation = Expectation(name)
        self._replay_session: ReplaySession | None = None
        self._recording_session: RecordingSession | None = None

    def returns(
        self, stdout: str = "", stderr: str = "", exit_code: int = 0
    ) -> typ.Self:
        """Set the static response and return ``self``.

        Parameters
        ----------
        stdout, stderr : str
            Output streams returned by the command.
        exit_code : int
            Exit status returned by the command.

        Returns
        -------
        typ.Self
            This double, for fluent configuration.
        """
        self.response = Response(stdout=stdout, stderr=stderr, exit_code=exit_code)
        self.handler = None
        return self

    def runs(
        self,
        handler: cabc.Callable[[Invocation], tuple[str, str, int] | Response],
    ) -> typ.Self:
        """Use *handler* to generate responses dynamically.

        Parameters
        ----------
        handler : collections.abc.Callable
            Function receiving an invocation and returning a ``Response`` or
            ``(stdout, stderr, exit_code)`` tuple.

        Returns
        -------
        typ.Self
            This double, for fluent configuration.

        """

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

    def with_args(self, *args: str) -> typ.Self:
        """Require the command to be invoked with *args*.

        Parameters
        ----------
        *args : str
            Exact command arguments expected at invocation time.

        Returns
        -------
        typ.Self
            This double, for fluent configuration.
        """
        self.expectation.with_args(*args)
        return self

    def with_matching_args(self, *matchers: cabc.Callable[[str], bool]) -> typ.Self:
        """Validate arguments using matcher predicates.

        Parameters
        ----------
        *matchers : collections.abc.Callable
            Predicates applied to corresponding command arguments.

        Returns
        -------
        typ.Self
            This double, for fluent configuration.
        """
        self.expectation.with_matching_args(*matchers)
        return self

    def with_stdin(self, data: str | cabc.Callable[[str], bool]) -> typ.Self:
        """Expect the given stdin ``data`` or matcher.

        Parameters
        ----------
        data : str or collections.abc.Callable
            Exact stdin text or predicate used for matching.

        Returns
        -------
        typ.Self
            This double, for fluent configuration.
        """
        self.expectation.with_stdin(data)
        return self

    def with_env(self, mapping: dict[str, str]) -> typ.Self:
        """Expect the provided environment mapping.

        Parameters
        ----------
        mapping : dict[str, str]
            Environment entries expected at invocation time.

        Returns
        -------
        typ.Self
            This double, for fluent configuration.
        """
        self.expectation.with_env(mapping)
        return self

    def times(self, count: int) -> typ.Self:
        """Require the command to be invoked exactly ``count`` times.

        Parameters
        ----------
        count : int
            Required invocation count.

        Returns
        -------
        typ.Self
            This double, for fluent configuration.
        """
        self.expectation.times(count)
        return self

    def times_called(self, count: int) -> typ.Self:
        """Verify the spy was called ``count`` times.

        Parameters
        ----------
        count : int
            Required invocation count.

        Returns
        -------
        typ.Self
            This double, for fluent configuration.
        """
        self.expectation.times_called(count)
        return self

    def in_order(self) -> typ.Self:
        """Mark this expectation as ordered.

        Returns
        -------
        typ.Self
            This double, for fluent configuration.
        """
        self.expectation.in_order()
        self._ensure_in_order()
        return self

    def any_order(self) -> typ.Self:
        """Mark this expectation as unordered.

        Returns
        -------
        typ.Self
            This double, for fluent configuration.
        """
        self.expectation.any_order()
        self._ensure_any_order()
        return self

    def passthrough(self) -> typ.Self:
        """Execute the real command while recording invocations.

        Returns
        -------
        typ.Self
            This spy, for fluent configuration.

        Raises
        ------
        ValueError
            If this double is not a spy or already has a replay session.
        """
        if self.kind is not DoubleKind.SPY:
            msg = "passthrough() is only valid for spies"
            raise ValueError(msg)
        if self._replay_session is not None:
            msg = "passthrough() cannot be combined with replay()"
            raise ValueError(msg)
        self.passthrough_mode = True
        return self

    def record(
        self,
        fixture_path: str | Path,
        *,
        scrubber: Scrubber | None = None,
        env_allowlist: list[str] | None = None,
    ) -> typ.Self:
        """Enable recording of passthrough invocations to a fixture file.

        Must be called after :meth:`passthrough`. Creates and starts a
        :class:`~cmd_mox.record.session.RecordingSession` that will capture
        each passthrough result via the coordinator.

        Parameters
        ----------
        fixture_path:
            Destination path for the fixture JSON file.
        scrubber:
            Optional scrubber for sanitizing recordings before persistence.
        env_allowlist:
            Environment variable keys to always include in recordings.

        Returns
        -------
        typ.Self
            This double, allowing further fluent configuration.

        Raises
        ------
        ValueError
            If passthrough mode is not enabled.
        RuntimeError
            If a recording session already exists.
        """
        if not self.passthrough_mode:
            msg = "record() requires passthrough(); call it first"
            raise ValueError(msg)
        if self._recording_session is not None:
            msg = "record() already called; finalize the existing session first"
            raise RuntimeError(msg)

        from pathlib import Path as _Path

        from .record.session import RecordingSession as _RecordingSession

        self._recording_session = _RecordingSession(
            fixture_path=_Path(fixture_path),
            scrubber=scrubber,
            env_allowlist=env_allowlist or [],
        )
        self._recording_session.start()
        return self

    def replay(
        self,
        fixture_path: str | Path,
        *,
        strict: bool = True,
    ) -> typ.Self:
        """Attach and eagerly load a replay fixture for a spy.

        Parameters
        ----------
        fixture_path : str or pathlib.Path
            Fixture JSON file to load.
        strict : bool
            Whether replay matching includes stdin and environment values.

        Returns
        -------
        typ.Self
            This spy, for fluent configuration.

        Raises
        ------
        FileNotFoundError
            If the replay fixture does not exist.
        ValueError
            If this double is not a spy, is configured for passthrough, or
            the replay fixture is invalid.
        RuntimeError
            If a replay session already exists.
        """  # ruff: ignore[docstring-extraneous-exception] - ReplaySession.load propagates fixture-load failures.
        if self.kind is not DoubleKind.SPY:
            msg = "replay() is only valid for spies"
            raise ValueError(msg)
        if self.passthrough_mode:
            msg = "replay() cannot be combined with passthrough()"
            raise ValueError(msg)
        if self._replay_session is not None:
            msg = "replay() already called; finalize the existing session first"
            raise RuntimeError(msg)

        from pathlib import Path as _Path

        from .record.replay import ReplaySession as _ReplaySession

        replay_session = _ReplaySession(
            fixture_path=_Path(fixture_path),
            strict_matching=strict,
        )
        replay_session.load()
        self._replay_session = replay_session
        return self

    @property
    def has_recording_session(self) -> bool:
        """Whether a recording session is attached.

        Returns
        -------
        bool
            ``True`` when recording is configured for this double.
        """
        return self._recording_session is not None

    @property
    def recording_session(self) -> RecordingSession | None:
        """The attached recording session, or ``None``.

        Returns
        -------
        RecordingSession or None
            Active recording session, if configured.
        """
        return self._recording_session

    @property
    def has_replay_session(self) -> bool:
        """Whether a replay session is attached.

        Returns
        -------
        bool
            ``True`` when replay is configured for this double.
        """
        return self._replay_session is not None

    @property
    def replay_session(self) -> ReplaySession | None:
        """The attached replay session, or ``None``.

        Returns
        -------
        ReplaySession or None
            Active replay session, if configured.
        """
        return self._replay_session

    # ------------------------------------------------------------------
    # Matching helpers
    # ------------------------------------------------------------------
    def matches(self, invocation: Invocation) -> bool:
        """Return whether *invocation* satisfies the expectation.

        The command name is checked before expectation matching so a double
        cannot accept an invocation owned by another command.

        Parameters
        ----------
        invocation : Invocation
            Invocation to compare with this double's expectation.

        Returns
        -------
        bool
            ``True`` when command and expectation criteria match.
        """
        if invocation.command != self.name:
            return False
        return self.expectation.matches(invocation)

    @property
    def is_expected(self) -> bool:
        """Whether this double is a mock.

        Returns
        -------
        bool
            ``True`` only for :attr:`DoubleKind.MOCK`.
        """
        return self.kind is DoubleKind.MOCK

    @property
    def is_recording(self) -> bool:
        """Whether this double records invocations.

        Returns
        -------
        bool
            ``True`` for mocks and spies.
        """
        return self.kind in {DoubleKind.MOCK, DoubleKind.SPY}

    @property
    def call_count(self) -> int:
        """The number of recorded invocations.

        Returns
        -------
        int
            Number of invocations recorded by this double.
        """
        return len(self.invocations)

    # ------------------------------------------------------------------
    # Spy assertions
    # ------------------------------------------------------------------
    def assert_called(self) -> None:
        """Raise ``AssertionError`` if this spy was never invoked.

        Raises
        ------
        AssertionError
            If this is not a spy or no invocation was recorded.
        """
        self._validate_spy_usage("assert_called")
        if not self.invocations:
            msg = (
                f"Expected {self.name!r} to be called at least once but it was"
                " never called"
            )
            raise AssertionError(msg)

    def assert_not_called(self) -> None:
        """Raise ``AssertionError`` if this spy was invoked.

        Raises
        ------
        AssertionError
            If this is not a spy or any invocation was recorded.
        """
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
        """Assert the most recent call used the given arguments and context.

        Parameters
        ----------
        *args : str
            Expected command arguments.
        stdin : str or None
            Expected stdin text, when supplied.
        env : dict[str, str] or None
            Expected environment mapping, when supplied.

        Raises
        ------
        AssertionError
            If this double is not a spy, no invocation has been recorded, or
            the most recent invocation's args, stdin (when supplied), or env
            (when supplied) differ from the expectations.

        """  # ruff: ignore[docstring-extraneous-exception] - AssertionError is raised by the validation helpers invoked below
        self._validate_spy_usage("assert_called_with")
        invocation = self._get_last_invocation()
        self._validate_arguments(invocation, args)
        self._validate_stdin(invocation, stdin)
        self._validate_environment(invocation, env)

    # ------------------------------------------------------------------
    # Spy assertion helpers
    # ------------------------------------------------------------------
    def _validate_spy_usage(self, method_name: str) -> None:
        if self.kind is not DoubleKind.SPY:  # pragma: no cover - defensive guard
            msg = f"{method_name}() is only valid for spies"
            raise AssertionError(msg)

    def _get_last_invocation(self) -> Invocation:
        if not self.invocations:
            msg = f"Expected {self.name!r} to be called but it was never called"
            raise AssertionError(msg)
        return self.invocations[-1]

    def _assert_equal[T](self, label: str, actual: T, expected: T) -> None:
        """Raise ``AssertionError`` if *actual* != *expected*.

        The *label* provides contextual information for the error message,
        yielding a consistent formatting across different validations.

        Raises
        ------
        AssertionError
            If ``actual`` and ``expected`` differ.
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
        """Return debugging representation with name, kind, and response.

        Returns
        -------
        str
            Debugging representation of this double.
        """
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
