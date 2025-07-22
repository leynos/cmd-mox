"""CmdMox controller and related helpers."""

from __future__ import annotations

import enum
import typing as t
from collections import deque

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types
    from pathlib import Path

    TracebackType = types.TracebackType

from .environment import EnvironmentManager
from .errors import (
    LifecycleError,
    MissingEnvironmentError,
    UnexpectedCommandError,
    UnfulfilledExpectationError,
)
from .ipc import Invocation, IPCServer, Response
from .shimgen import create_shim_symlinks


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

    __slots__ = ("controller", "invocations", "kind", "name", "response")

    T_Kind = t.Literal["stub", "mock", "spy"]

    def __init__(self, name: str, controller: "CmdMox", kind: T_Kind) -> None:  # noqa: UP037
        self.name = name
        self.kind = kind
        self.controller = controller
        self.response = Response()
        self.invocations: list[Invocation] = []

    T_Self = t.TypeVar("T_Self", bound="CommandDouble")

    def returns(
        self: T_Self, stdout: str = "", stderr: str = "", exit_code: int = 0
    ) -> T_Self:
        """Set the static response and return ``self``."""
        self.response = Response(stdout=stdout, stderr=stderr, exit_code=exit_code)
        return self

    @property
    def is_expected(self) -> bool:
        """Return ``True`` for stubs and mocks."""
        return self.kind in ("stub", "mock")

    @property
    def is_recording(self) -> bool:
        """Return ``True`` for mocks and spies."""
        return self.kind in ("mock", "spy")

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
        self._entered = False
        self._phase = Phase.RECORD

        self._verify_on_exit = verify_on_exit

        self._doubles: dict[str, CommandDouble] = {}
        self.journal: deque[Invocation] = deque()
        self._commands: set[str] = set()

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
        """Transition to replay mode and start the IPC server.

        The context must be entered before calling this method.
        """
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
        try:
            self.journal.clear()
            self._commands = self._registered_commands() | self._commands
            create_shim_symlinks(self.environment.shim_dir, self._commands)
            self._server = _CallbackIPCServer(
                self.environment.socket_path, self._handle_invocation
            )
            self._server.start()
            self._phase = Phase.REPLAY
        except Exception:
            if self._server is not None:
                try:
                    self._server.stop()
                finally:
                    self._server = None
            self.environment.__exit__(None, None, None)
            self._entered = False
            raise

    def verify(self) -> None:
        """Stop the server and finalise the verification phase."""
        if self._phase is not Phase.REPLAY:
            msg = (
                "verify() called out of order "
                f"(current phase: {self._phase.name.lower()})"
            )
            raise LifecycleError(msg)
        try:
            all_registered = self._registered_commands()
            unexpected = [
                inv.command for inv in self.journal if inv.command not in all_registered
            ]
            if unexpected:
                msg = f"Unexpected commands invoked: {unexpected}"
                raise UnexpectedCommandError(msg)
            required = self._expected_commands()
            missing = [
                name
                for name in required
                if all(inv.command != name for inv in self.journal)
            ]
            if missing:
                msg = f"Expected commands not called: {missing}"
                raise UnfulfilledExpectationError(msg)
        finally:
            if self._server is not None:
                try:
                    self._server.stop()
                finally:
                    self._server = None
            if self._entered:
                self.environment.__exit__(None, None, None)
                self._entered = False
            self._phase = Phase.VERIFY

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _handle_invocation(self, invocation: Invocation) -> Response:
        """Record *invocation* and return the configured response."""
        self.journal.append(invocation)
        dbl = self._doubles.get(invocation.command)
        if not dbl:
            return Response(stdout=invocation.command)
        if dbl.is_recording:
            dbl.invocations.append(invocation)
        return dbl.response
