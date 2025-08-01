"""CmdMox controller and related helpers."""

from __future__ import annotations

import enum
import typing as t
from collections import deque

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types
    from pathlib import Path

    TracebackType = types.TracebackType

from .command_runner import CommandRunner
from .environment import EnvironmentManager, temporary_env
from .errors import LifecycleError, MissingEnvironmentError
from .expectations import Expectation
from .ipc import Invocation, IPCServer, Response
from .shimgen import create_shim_symlinks
from .verifiers import CountVerifier, OrderVerifier, UnexpectedCommandVerifier


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


class CommandDouble:
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

    T_Self = t.TypeVar("T_Self", bound="CommandDouble")

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
    # Expectation configuration
    # ------------------------------------------------------------------
    def with_args(self: T_Self, *args: str) -> T_Self:
        """Match invocations with exactly ``args``."""
        self.expectation.with_args(*args)
        return self

    def with_matching_args(self: T_Self, *matchers: t.Callable[[str], bool]) -> T_Self:
        """Match invocations using comparator callables."""
        self.expectation.with_matching_args(*matchers)
        return self

    def with_stdin(self: T_Self, data: str | t.Callable[[str], bool]) -> T_Self:
        """Expect standard input to match ``data``."""
        self.expectation.with_stdin(data)
        return self

    def times(self: T_Self, count: int) -> T_Self:
        """Expect exactly ``count`` invocations."""
        self.expectation.times_called(count)
        return self

    def in_order(self: T_Self) -> T_Self:
        """Require this mock to be called in registration order."""
        if self.expectation not in self.controller._ordered:
            self.controller._ordered.append(self.expectation)
        self.expectation.in_order()
        return self

    def any_order(self: T_Self) -> T_Self:
        """Allow this mock to be called in any order."""
        if self.expectation in self.controller._ordered:
            self.controller._ordered.remove(self.expectation)
        self.expectation.any_order()
        return self

    def with_env(self: T_Self, mapping: dict[str, str]) -> T_Self:
        """Inject additional environment variables when invoked."""
        self.expectation.with_env(mapping)
        return self

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
        self.environment.__enter__()
        self._entered = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:  # pragma: no cover - thin wrapper
        """Exit context, optionally verifying and cleaning up."""
        if self._handle_auto_verify(exc_type):
            return

        self._stop_server_and_exit_env(exc_type, exc, tb)

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
        if double.handler is None:
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

    def _check_replay_preconditions(self) -> None:
        """Validate state and environment before starting replay."""
        if self._phase is not Phase.RECORD:
            msg = (
                "Cannot call replay(): not in 'record' phase "
                f"(current phase: {self._phase.name.lower()})"
            )
            raise LifecycleError(msg)
        if not self._entered:
            msg = (
                "replay() called without entering context "
                f"(current phase: {self._phase.name.lower()})"
            )
            raise LifecycleError(msg)
        if self.environment.shim_dir is None:
            msg = "Environment attribute 'shim_dir' is missing (None)"
            raise MissingEnvironmentError(msg)
        if self.environment.socket_path is None:
            msg = "Environment attribute 'socket_path' is missing (None)"
            raise MissingEnvironmentError(msg)

    def _start_ipc_server(self) -> None:
        """Prepare shims and launch the IPC server."""
        self.journal.clear()
        self._commands = self._registered_commands() | self._commands
        create_shim_symlinks(self.environment.shim_dir, self._commands)
        self._server = _CallbackIPCServer(
            self.environment.socket_path, self._handle_invocation
        )
        self._server.start()

    def _stop_server_and_exit_env(
        self,
        exc_type: type[BaseException] | None = None,
        exc: BaseException | None = None,
        tb: TracebackType | None = None,
    ) -> None:
        """Stop the IPC server and exit the environment manager.

        This helper underpins :py:meth:`__exit__`,
        :py:meth:`_cleanup_after_replay_error`, and
        :py:meth:`_finalize_verification`.

        Parameters
        ----------
        exc_type, exc, tb:
            Exception information forwarded to
            :meth:`EnvironmentManager.__exit__`. Pass ``None`` for all three
            when no exception is being handled.

        This method is idempotent so it is safe to call multiple times from
        different cleanup paths.
        """
        if self._server is not None:
            try:
                self._server.stop()
            finally:
                self._server = None

        if self._entered:
            self.environment.__exit__(exc_type, exc, tb)
            self._entered = False

    def _cleanup_after_replay_error(self) -> None:
        """Stop the server and restore the environment after failure."""
        self._stop_server_and_exit_env()

    def _check_verify_preconditions(self) -> None:
        """Ensure verify() is called in the correct phase."""
        if self._phase is not Phase.REPLAY:
            msg = (
                "verify() called out of order "
                f"(current phase: {self._phase.name.lower()})"
            )
            raise LifecycleError(msg)

    def _run_verifiers(self) -> None:
        """Execute the ordered verification checks."""
        expectations = {n: d.expectation for n, d in self.mocks.items()}
        inv_map = {n: d.invocations for n, d in self.mocks.items()}

        UnexpectedCommandVerifier().verify(self.journal, self._doubles)
        OrderVerifier(self._ordered).verify(self.journal)
        CountVerifier().verify(expectations, inv_map)

    def _finalize_verification(self) -> None:
        """Stop the server, clean up the environment, and update phase."""
        self._stop_server_and_exit_env()
        self._phase = Phase.VERIFY
