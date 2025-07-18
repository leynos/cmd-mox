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


class StubCommand:
    """Simple stub configuration object."""

    def __init__(self, name: str, controller: CmdMox) -> None:  # type: ignore[NAME_DEFINED_LATER]
        self.name = name
        self.controller = controller
        self.response = Response()

    def returns(
        self, stdout: str = "", stderr: str = "", exit_code: int = 0
    ) -> StubCommand:
        """Set the static response for this stub."""
        self.response = Response(stdout=stdout, stderr=stderr, exit_code=exit_code)
        return self


class MockCommand(StubCommand):
    """Placeholder strict mock command."""


class SpyCommand:
    """Simple spy configuration object that records invocations."""

    def __init__(self, name: str, controller: CmdMox) -> None:  # type: ignore[NAME_DEFINED_LATER]
        self.name = name
        self.controller = controller
        self.response = Response()
        self.invocations: list[Invocation] = []

    def returns(
        self, stdout: str = "", stderr: str = "", exit_code: int = 0
    ) -> SpyCommand:
        """Set the static response for this spy."""
        self.response = Response(stdout=stdout, stderr=stderr, exit_code=exit_code)
        return self


class Phase(enum.Enum):
    """Lifecycle phases for :class:`CmdMox`."""

    RECORD = enum.auto()
    REPLAY = enum.auto()
    VERIFY = enum.auto()


class CmdMox:
    """Central orchestrator implementing the record-replay-verify lifecycle."""

    def __init__(self) -> None:
        self.environment = EnvironmentManager()
        self._server: _CallbackIPCServer | None = None
        self._entered = False
        self._phase = Phase.RECORD

        self.stubs: dict[str, StubCommand] = {}
        self.mocks: dict[str, MockCommand] = {}
        self.spies: dict[str, SpyCommand] = {}
        self.journal: deque[Invocation] = deque()
        self._commands: set[str] = set()

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
        """Exit context and clean up the environment."""
        if self._server is not None:
            try:
                self._server.stop()
            finally:
                self._server = None
        if self._entered:
            self.environment.__exit__(exc_type, exc, tb)
            self._entered = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def register_command(self, name: str) -> None:
        """Register *name* for shim creation during :meth:`replay`."""
        self._commands.add(name)

    def stub(self, command_name: str) -> StubCommand:
        """Create or retrieve a :class:`StubCommand` for *command_name*."""
        if command_name in self.stubs:
            return self.stubs[command_name]
        stub = StubCommand(command_name, self)
        self.stubs[command_name] = stub
        self.register_command(command_name)
        return stub

    def mock(self, command_name: str) -> MockCommand:
        """Create or retrieve a :class:`MockCommand` for *command_name*."""
        if command_name in self.mocks:
            return self.mocks[command_name]
        mock = MockCommand(command_name, self)
        self.mocks[command_name] = mock
        self.register_command(command_name)
        return mock

    def spy(self, command_name: str) -> SpyCommand:
        """Create or retrieve a :class:`SpyCommand` for *command_name*."""
        if command_name in self.spies:
            return self.spies[command_name]
        spy = SpyCommand(command_name, self)
        self.spies[command_name] = spy
        self.register_command(command_name)
        return spy

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
            self._commands = (
                set(self.stubs) | set(self.mocks) | set(self.spies) | self._commands
            )
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
            all_registered = set(self.stubs) | set(self.mocks) | set(self.spies)
            unexpected = [
                inv.command for inv in self.journal if inv.command not in all_registered
            ]
            if unexpected:
                msg = f"Unexpected commands invoked: {unexpected}"
                raise UnexpectedCommandError(msg)
            required = set(self.stubs) | set(self.mocks)
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
        """Record *invocation* and return the appropriate response."""
        self.journal.append(invocation)
        if invocation.command in self.stubs:
            return self.stubs[invocation.command].response
        if invocation.command in self.mocks:
            return self.mocks[invocation.command].response
        if invocation.command in self.spies:
            spy = self.spies[invocation.command]
            spy.invocations.append(invocation)
            return spy.response
        return Response(stdout=invocation.command)
