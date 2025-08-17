"""CmdMox controller and related helpers."""

from __future__ import annotations

import enum
import typing as t
from collections import deque

if t.TYPE_CHECKING:  # pragma: no cover
    from pathlib import Path

from .command_runner import CommandRunner
from .environment import EnvironmentManager, temporary_env
from .errors import LifecycleError, MissingEnvironmentError
from .expectations import Expectation
from .ipc import Invocation, IPCServer, Response
from .shimgen import create_shim_symlinks
from .verifiers import CountVerifier, OrderVerifier, UnexpectedCommandVerifier

_ExpectationProxy: t.Any
TracebackType: t.Any
_ORDER_HANDLERS: dict[str, t.Callable[[t.Any], None]]


def _initialize_type_system() -> tuple[type, t.Any]:
    """Initialize type checking components and return them."""
    if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
        import types
        from pathlib import Path  # noqa: F401

        TracebackType = types.TracebackType  # noqa: N806

        class _ExpectationProxy(t.Protocol):
            def with_args(self: T_Self, *args: str) -> T_Self: ...

            def with_matching_args(
                self: T_Self, *matchers: t.Callable[[str], bool]
            ) -> T_Self: ...

            def with_stdin(
                self: T_Self, data: str | t.Callable[[str], bool]
            ) -> T_Self: ...

            def with_env(self: T_Self, mapping: dict[str, str]) -> T_Self: ...

            def times(self: T_Self, count: int) -> T_Self: ...

            def times_called(self: T_Self, count: int) -> T_Self: ...

            def in_order(self: T_Self) -> T_Self: ...

            def any_order(self: T_Self) -> T_Self: ...

        return _ExpectationProxy, TracebackType

    # pragma: no cover - runtime placeholder
    class _ExpectationProxy:
        pass

    return _ExpectationProxy, None


T_Self = t.TypeVar("T_Self", bound="CommandDouble")


_ExpectationProxy, TracebackType = _initialize_type_system()


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


class CommandDouble(_ExpectationProxy):  # type: ignore[unsupported-base]
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

    def returns(
        self: T_Self, stdout: str = "", stderr: str = "", exit_code: int = 0
    ) -> T_Self:
        """Set the static response and return ``self``."""
        self.response = Response(stdout=stdout, stderr=stderr, exit_code=exit_code)
        self.handler = None
        return self

    def runs(
        self: T_Self,
        handler: t.Callable[[Invocation], tuple[str, str, int] | Response],
    ) -> T_Self:
        """Use *handler* to generate responses dynamically."""

        def _wrap(invocation: Invocation) -> Response:
            result = handler(invocation)
            if isinstance(result, Response):
                return result
            stdout, stderr, exit_code = t.cast("tuple[str, str, int]", result)
            return Response(stdout=stdout, stderr=stderr, exit_code=exit_code)

        self.handler = _wrap
        return self

    # ------------------------------------------------------------------
    # Expectation configuration via delegation
    # ------------------------------------------------------------------
    _DELEGATED_METHODS: t.ClassVar[dict[str, str]] = {
        "with_args": "with_args",
        "with_matching_args": "with_matching_args",
        "with_stdin": "with_stdin",
        "with_env": "with_env",
        "times": "times",
        "times_called": "times_called",
        "in_order": "in_order",
        "any_order": "any_order",
    }

    def _ensure_in_order(self) -> None:
        """Register this expectation for ordered verification."""
        if self.expectation not in self.controller._ordered:
            self.controller._ordered.append(self.expectation)

    def _ensure_any_order(self) -> None:
        """Remove this expectation from ordered verification."""
        if self.expectation in self.controller._ordered:
            self.controller._ordered.remove(self.expectation)

    def passthrough(self: T_Self) -> T_Self:
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
                f" {len(self.invocations)} time(s); last args={last.args!r}"
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

    def _validate_arguments(
        self, invocation: Invocation, expected_args: tuple[str, ...]
    ) -> None:
        if list(expected_args) != list(invocation.args):
            msg = (
                f"{self.name!r} called with args {invocation.args!r}, "
                f"expected {list(expected_args)!r}"
            )
            raise AssertionError(msg)

    def _validate_stdin(
        self, invocation: Invocation, expected_stdin: str | None
    ) -> None:
        if expected_stdin is not None and invocation.stdin != expected_stdin:
            msg = (
                f"{self.name!r} called with stdin {invocation.stdin!r}, "
                f"expected {expected_stdin!r}"
            )
            raise AssertionError(msg)

    def _validate_environment(
        self, invocation: Invocation, expected_env: dict[str, str] | None
    ) -> None:
        if expected_env is not None and invocation.env != expected_env:
            msg = (
                f"{self.name!r} called with env {invocation.env!r}, "
                f"expected {expected_env!r}"
            )
            raise AssertionError(msg)

    def __repr__(self) -> str:
        """Return debugging representation with name, kind, and response."""
        return (
            f"CommandDouble(name={self.name!r}, "
            f"kind={self.kind!r}, "
            f"response={self.response!r})"
        )

    __str__ = __repr__


def _create_order_handlers() -> dict[str, t.Callable[[CommandDouble], None]]:
    """Create the order handlers dictionary."""
    return {
        "in_order": lambda self: self._ensure_in_order(),
        "any_order": lambda self: self._ensure_any_order(),
    }


def _setup_delegated_methods() -> None:
    """Set up proxy methods on CommandDouble for expectation delegation."""

    def make_proxy(
        method_name: str, expectation_method: str
    ) -> t.Callable[..., CommandDouble]:
        order_handler = _ORDER_HANDLERS.get(method_name, lambda self: None)

        def proxy(
            self: CommandDouble, *args: object, **kwargs: object
        ) -> CommandDouble:
            getattr(self.expectation, expectation_method)(*args, **kwargs)
            order_handler(self)
            return self

        proxy.__name__ = method_name
        return proxy

    for method_name, expectation_method in CommandDouble._DELEGATED_METHODS.items():
        setattr(CommandDouble, method_name, make_proxy(method_name, expectation_method))


def _initialize_module_components() -> None:
    """Initialize all module-level components to reduce global complexity."""
    global _ExpectationProxy, TracebackType, _ORDER_HANDLERS
    _ExpectationProxy, TracebackType = _initialize_type_system()
    _ORDER_HANDLERS = _create_order_handlers()
    _setup_delegated_methods()


_initialize_module_components()


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

    def __init__(self, *, verify_on_exit: bool = True) -> None:
        """Create a new controller.

        Parameters
        ----------
        verify_on_exit:
            When ``True`` (the default), :meth:`__exit__` will automatically
            call :meth:`verify`. This catches missed verifications and ensures
            the environment is restored. Disable for explicit control.
        """
        self.environment = EnvironmentManager()
        self._server: _CallbackIPCServer | None = None
        self._runner = CommandRunner(self.environment)
        self._entered = False
        self._phase = Phase.RECORD

        self._verify_on_exit = verify_on_exit

        self._doubles: dict[str, CommandDouble] = {}
        self.journal: deque[Invocation] = deque()
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
        tb: TracebackType | None,
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
        """Register *name* for shim creation during :meth:`replay`."""
        self._commands.add(name)

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
            resp = double.response
        elif env:
            with temporary_env(env):
                resp = double.handler(invocation)
        else:
            resp = double.handler(invocation)
        if env:
            resp.env.update(env)
        return resp

    def _handle_invocation(self, invocation: Invocation) -> Response:
        """Record *invocation* and return the configured response."""
        self.journal.append(invocation)
        double = self._doubles.get(invocation.command)
        if not double:
            return Response(stdout=invocation.command)
        if double.is_recording:
            double.invocations.append(invocation)
        return self._invoke_handler(double, invocation)

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
