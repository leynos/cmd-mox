"""CmdMox controller and related helpers."""

from __future__ import annotations

import dataclasses as dc
import enum
import types  # noqa: TC003
import typing as t
from collections import deque
from pathlib import Path

from typing_extensions import Self

from .command_runner import CommandRunner
from .environment import EnvironmentManager, temporary_env
from .errors import LifecycleError, MissingEnvironmentError
from .expectations import Expectation
from .ipc import Invocation, IPCServer, Response
from .shimgen import create_shim_symlinks
from .verifiers import CountVerifier, OrderVerifier, UnexpectedCommandVerifier

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
        def with_args(self, *args: str) -> Self:
            raise NotImplementedError("with_args is typing-only")

        def with_matching_args(self, *matchers: t.Callable[[str], bool]) -> Self:
            raise NotImplementedError("with_matching_args is typing-only")

        def with_stdin(self, data: str | t.Callable[[str], bool]) -> Self:
            raise NotImplementedError("with_stdin is typing-only")

        def with_env(self, mapping: dict[str, str]) -> Self:
            raise NotImplementedError("with_env is typing-only")

        def times(self, count: int) -> Self:
            raise NotImplementedError("times is typing-only")

        def times_called(self, count: int) -> Self:
            raise NotImplementedError("times_called is typing-only")

        def in_order(self) -> Self:
            raise NotImplementedError("in_order is typing-only")

        def any_order(self) -> Self:
            raise NotImplementedError("any_order is typing-only")

    return _ExpectationProxy


_ExpectationProxy = _create_expectation_proxy()


class _CallbackIPCServer(IPCServer):
    """IPCServer variant that delegates to a callback."""

    def __init__(
        self, socket_path: Path, handler: t.Callable[[Invocation], Response]
    ) -> None:
        super().__init__(socket_path)
        self._handler = handler

    def handle_invocation(
        self, invocation: Invocation
    ) -> Response:  # pragma: no cover - wrapper
        return self._handler(invocation)


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

    T_Kind = t.Literal["stub", "mock", "spy"]

    def __init__(self, name: str, controller: "CmdMox", kind: T_Kind) -> None:  # noqa: UP037
        self.name = name
        self.kind = kind
        self.controller = controller
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


class Phase(enum.Enum):
    """Lifecycle phases for :class:`CmdMox`."""

    RECORD = enum.auto()
    REPLAY = enum.auto()
    VERIFY = enum.auto()


class CmdMox:
    """Central orchestrator implementing the record-replay-verify lifecycle."""

    def __init__(
        self,
        *,
        verify_on_exit: bool = True,
        max_journal_entries: int | None = None,
        environment: EnvironmentManager | None = None,
    ) -> None:
        """Create a new controller.

        Parameters
        ----------
        verify_on_exit:
            When ``True`` (the default), :meth:`__exit__` will automatically
            call :meth:`verify`. This catches missed verifications and ensures
            the environment is restored. Disable for explicit control.
        max_journal_entries:
            Maximum number of invocations retained in the journal. When ``None``,
            the journal is unbounded. Older entries are discarded once the limit
            is exceeded. The journal is cleared at the start of each ``replay()``.
        environment:
            Optional :class:`EnvironmentManager` instance used to prepare shim and
            PATH state. When omitted a fresh manager is created automatically.
        """
        self.environment = (
            environment if environment is not None else EnvironmentManager()
        )
        self._server: _CallbackIPCServer | None = None
        self._runner = CommandRunner(self.environment)
        self._entered = False
        self._phase = Phase.RECORD

        if max_journal_entries is not None and max_journal_entries <= 0:
            msg = "max_journal_entries must be positive"
            raise ValueError(msg)

        self._verify_on_exit = verify_on_exit

        self._doubles: dict[str, CommandDouble] = {}
        self.journal: deque[Invocation] = deque(maxlen=max_journal_entries)
        self._commands: set[str] = set()
        self._ordered: list[Expectation] = []

    # ------------------------------------------------------------------
    # Double accessors
    # ------------------------------------------------------------------
    @property
    def stubs(self) -> dict[str, CommandDouble]:
        """Return all stub doubles."""
        return {n: d for n, d in self._doubles.items() if d.kind == "stub"}

    @property
    def mocks(self) -> dict[str, CommandDouble]:
        """Return all mock doubles."""
        return {n: d for n, d in self._doubles.items() if d.kind == "mock"}

    @property
    def spies(self) -> dict[str, CommandDouble]:
        """Return all spy doubles."""
        return {n: d for n, d in self._doubles.items() if d.kind == "spy"}

    # ------------------------------------------------------------------
    # Lifecycle state
    # ------------------------------------------------------------------
    @property
    def phase(self) -> Phase:
        """Return the current lifecycle phase."""
        return self._phase

    # ------------------------------------------------------------------
    # Internal helper accessors
    # ------------------------------------------------------------------
    def _registered_commands(self) -> set[str]:
        """Return all commands registered via doubles."""
        return set(self._doubles)

    def _expected_commands(self) -> set[str]:
        """Return commands that must be called during replay."""
        return {name for name, dbl in self._doubles.items() if dbl.is_expected}

    # ------------------------------------------------------------------
    # Context manager protocol
    # ------------------------------------------------------------------
    def __enter__(self) -> CmdMox:
        """Enter context, applying environment changes."""
        try:
            self.environment.__enter__()
        except RuntimeError:
            self._entered = False
            raise

        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: types.TracebackType | None,
    ) -> None:
        """Exit context, optionally verifying and cleaning up."""
        if self._handle_auto_verify(exc_type):
            return
        if self._server is not None:
            try:
                self._server.stop()
            finally:
                self._server = None
        if self._entered:
            self.environment.__exit__(exc_type, exc, tb)
            self._entered = False

    def _handle_auto_verify(self, exc_type: type[BaseException] | None) -> bool:
        """Invoke :meth:`verify` when exiting a replay block."""
        if not self._verify_on_exit or self._phase is not Phase.REPLAY:
            return False
        verify_error: Exception | None = None
        try:
            self.verify()
        except Exception as err:  # noqa: BLE001
            # pragma: no cover - verification failed
            verify_error = err
        if exc_type is None and verify_error is not None:
            raise verify_error
        # Early return is safe: verify() handles server shutdown and environment cleanup
        return True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def register_command(self, name: str) -> None:
        """Register *name* and ensure a shim exists during :meth:`replay`.

        The command name is recorded for future replay transitions. When the
        controller is already in :class:`Phase.REPLAY` and the environment has
        been entered (``env.shim_dir`` is populated), the shim symlink is
        created immediately so late-registered doubles work without restarting
        the IPC server. Existing symlinks are left untouched, and subsequent
        :meth:`_start_ipc_server` calls re-sync every shim so repeated
        registrations remain idempotent.
        """
        self._commands.add(name)
        self._ensure_shim_during_replay(name)

    def _ensure_shim_during_replay(self, name: str) -> None:
        """Create a shim symlink when replay is active and shims are writable."""
        if self._phase is not Phase.REPLAY:
            return
        env = self.environment
        if env is None or env.shim_dir is None:
            return
        shim_dir = Path(env.shim_dir)
        shim_path = shim_dir / name
        if shim_path.is_symlink():
            return
        if shim_path.exists():
            msg = f"{shim_path} already exists and is not a symlink"
            raise FileExistsError(msg)
        create_shim_symlinks(shim_dir, [name])

    def _get_double(
        self, command_name: str, kind: CommandDouble.T_Kind
    ) -> CommandDouble:
        dbl = self._doubles.get(command_name)
        if dbl is None:
            dbl = CommandDouble(command_name, self, kind)
            self._doubles[command_name] = dbl
            self.register_command(command_name)
        elif dbl.kind != kind:
            msg = (
                f"{command_name!r} already registered as {dbl.kind}; "
                f"cannot register as {kind}"
            )
            raise ValueError(msg)
        return dbl

    def stub(self, command_name: str) -> CommandDouble:
        """Create or retrieve a stub for *command_name*."""
        return self._get_double(command_name, "stub")

    def mock(self, command_name: str) -> CommandDouble:
        """Create or retrieve a mock for *command_name*."""
        return self._get_double(command_name, "mock")

    def spy(self, command_name: str) -> CommandDouble:
        """Create or retrieve a spy for *command_name*."""
        return self._get_double(command_name, "spy")

    def replay(self) -> None:
        """Transition to replay mode and start the IPC server."""
        self._check_replay_preconditions()
        try:
            self._start_ipc_server()
            self._phase = Phase.REPLAY
        except Exception:  # pragma: no cover - cleanup only
            self._cleanup_after_replay_error()
            raise

    def verify(self) -> None:
        """Stop the server and finalise the verification phase."""
        self._check_verify_preconditions()
        try:
            self._run_verifiers()
        finally:
            self._finalize_verification()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _invoke_handler(
        self, double: CommandDouble, invocation: Invocation
    ) -> Response:
        """Run ``double``'s handler within its expectation environment."""
        env = double.expectation.env
        if double.passthrough_mode:
            resp = self._runner.run(invocation, env)
        elif double.handler is None:
            base = double.response
            # Clone to avoid mutating the shared static response instance
            resp = dc.replace(base, env=dict(base.env))
        elif env:
            with temporary_env(env):
                resp = double.handler(invocation)
        else:
            resp = double.handler(invocation)
        if env:
            resp.env.update(env)
        return resp

    def _make_response(self, invocation: Invocation) -> Response:
        double = self._doubles.get(invocation.command)
        if double is None:
            resp = Response(stdout=invocation.command)
        else:
            resp = self._invoke_handler(double, invocation)
            if double.is_recording:
                double.invocations.append(invocation)
        invocation.apply(resp)
        return resp

    def _handle_invocation(self, invocation: Invocation) -> Response:
        """Record *invocation* and return the configured response."""
        resp = self._make_response(invocation)
        self.journal.append(invocation)
        return resp

    def _require_phase(self, expected: Phase, action: str) -> None:
        """Ensure we're in ``expected`` phase before executing ``action``."""
        if self._phase != expected:
            msg = (
                f"Cannot call {action}(): not in '{expected.name.lower()}' phase "
                f"(current phase: {self._phase.name.lower()})"
            )
            raise LifecycleError(msg)

    def _require_env_attrs(self, *attrs: str) -> None:
        """Ensure all referenced ``EnvironmentManager`` attributes exist."""
        env = self.environment
        if env is None:  # pragma: no cover - defensive guard
            raise MissingEnvironmentError
        missing = [attr for attr in attrs if getattr(env, attr) is None]
        if missing:
            missing_list = ", ".join(missing)
            msg = f"Missing environment attributes: {missing_list}"
            raise MissingEnvironmentError(msg)

    def _check_replay_preconditions(self) -> None:
        """Validate state and environment before starting replay."""
        self._require_phase(Phase.RECORD, "replay")
        if not self._entered:
            msg = (
                "replay() called without entering context "
                f"(current phase: {self._phase.name.lower()})"
            )
            raise LifecycleError(msg)
        self._require_env_attrs("shim_dir", "socket_path")

    def _start_ipc_server(self) -> None:
        """Prepare shims and launch the IPC server."""
        self.journal.clear()
        self._commands = self._registered_commands() | self._commands
        create_shim_symlinks(self.environment.shim_dir, self._commands)
        self._server = _CallbackIPCServer(
            self.environment.socket_path, self._handle_invocation
        )
        self._server.start()

    def _cleanup_after_replay_error(self) -> None:
        """Stop the server and restore the environment after failure."""
        self.__exit__(None, None, None)

    def _check_verify_preconditions(self) -> None:
        """Ensure verify() is called in the correct phase."""
        self._require_phase(Phase.REPLAY, "verify")
        self._require_env_attrs("shim_dir", "socket_path")

    def _run_verifiers(self) -> None:
        """Execute the ordered verification checks."""
        expectations = {n: d.expectation for n, d in self.mocks.items()}
        inv_map = {n: d.invocations for n, d in self.mocks.items()}

        UnexpectedCommandVerifier().verify(self.journal, self._doubles)
        OrderVerifier(self._ordered).verify(self.journal)
        CountVerifier().verify(expectations, inv_map)

    def _finalize_verification(self) -> None:
        """Stop the server, clean up the environment, and update phase."""
        verify_on_exit = self._verify_on_exit
        self._verify_on_exit = False
        try:
            self.__exit__(None, None, None)
        finally:
            self._verify_on_exit = verify_on_exit

        self._phase = Phase.VERIFY
