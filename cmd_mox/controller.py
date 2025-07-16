"""CmdMox controller and related helpers."""

from __future__ import annotations

import typing as t
from collections import deque

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import types

    TracebackType = types.TracebackType

from .environment import EnvironmentManager
from .ipc import Invocation, IPCServer, Response
from .shimgen import create_shim_symlinks


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


class CmdMox:
    """Central orchestrator implementing the record-replay-verify lifecycle."""

    def __init__(self) -> None:
        self.environment = EnvironmentManager()
        self._server: IPCServer | None = None
        self._entered = False
        self._phase = "record"

        self.expectations: list[object] = []
        self.stubs: dict[str, StubCommand] = {}
        self.spies: list[object] = []
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
            self._server.stop()
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

    def replay(self) -> None:
        """Transition to replay mode and start the IPC server."""
        if self._phase != "record":
            raise RuntimeError("replay() called out of order")  # noqa: TRY003
        if not self._entered:
            self.__enter__()
        if self.environment.shim_dir is None or self.environment.socket_path is None:
            raise RuntimeError("Environment not initialised")  # noqa: TRY003
        self.journal.clear()
        self._commands = set(self.stubs) | self._commands
        create_shim_symlinks(self.environment.shim_dir, self._commands)
        self._server = IPCServer(self.environment.socket_path)
        self._server.handle_invocation = self._handle_invocation  # type: ignore[method-assign]
        self._server.start()
        self._phase = "replay"

    def verify(self) -> None:
        """Stop the server and finalise the verification phase."""
        if self._phase != "replay":
            raise RuntimeError("verify() called out of order")  # noqa: TRY003
        try:
            unexpected = [
                inv.command for inv in self.journal if inv.command not in self.stubs
            ]
            if unexpected:
                msg = f"Unexpected commands invoked: {unexpected}"
                raise AssertionError(msg)
            missing = [
                name
                for name in self.stubs
                if all(inv.command != name for inv in self.journal)
            ]
            if missing:
                msg = f"Expected commands not called: {missing}"
                raise AssertionError(msg)
        finally:
            if self._server is not None:
                self._server.stop()
                self._server = None
            if self._entered:
                self.environment.__exit__(None, None, None)
                self._entered = False
            self._phase = "verify"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _handle_invocation(self, invocation: Invocation) -> Response:
        """Record *invocation* and return the appropriate response."""
        self.journal.append(invocation)
        stub = self.stubs.get(invocation.command)
        if stub is not None:
            return stub.response
        return Response(stdout=invocation.command)
