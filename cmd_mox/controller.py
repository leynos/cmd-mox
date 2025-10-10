"""CmdMox controller and related helpers."""

from __future__ import annotations

import dataclasses as dc
import enum
import os
import types  # noqa: TC003
import typing as t
from collections import deque
from pathlib import Path

from .command_runner import CommandRunner
from .environment import EnvironmentManager, temporary_env
from .errors import LifecycleError, MissingEnvironmentError
from .ipc import CallbackIPCServer, Invocation, PassthroughResult, Response
from .passthrough import PassthroughCoordinator
from .shimgen import create_shim_symlinks
from .test_doubles import CommandDouble, DoubleKind
from .verifiers import CountVerifier, OrderVerifier, UnexpectedCommandVerifier

if t.TYPE_CHECKING:
    from .expectations import Expectation


class Phase(enum.StrEnum):
    """Lifecycle phases for :class:`CmdMox`."""

    RECORD = "RECORD"
    REPLAY = "REPLAY"
    VERIFY = "VERIFY"


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
        self._server: CallbackIPCServer | None = None
        self._runner = CommandRunner(self.environment)
        self._entered = False
        self._phase = Phase.RECORD
        self._passthrough_coordinator = PassthroughCoordinator()

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
        return {
            name: dbl
            for name, dbl in self._doubles.items()
            if dbl.kind is DoubleKind.STUB
        }

    @property
    def mocks(self) -> dict[str, CommandDouble]:
        """Return all mock doubles."""
        return {
            name: dbl
            for name, dbl in self._doubles.items()
            if dbl.kind is DoubleKind.MOCK
        }

    @property
    def spies(self) -> dict[str, CommandDouble]:
        """Return all spy doubles."""
        return {
            name: dbl
            for name, dbl in self._doubles.items()
            if dbl.kind is DoubleKind.SPY
        }

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
        the IPC server. Healthy symlinks are left untouched, while broken links
        are recreated to repair them. Subsequent :meth:`_start_ipc_server` calls
        re-sync every shim so repeated registrations remain idempotent.
        """
        self._commands.add(name)
        self._ensure_shim_during_replay(name)

    def _ensure_shim_during_replay(self, name: str) -> None:
        """Create a shim symlink when replay is active and shims are writable."""
        if self._phase is not Phase.REPLAY:
            return
        shim_path = self._get_replay_shim_path(name)
        if shim_path is None:
            return
        if self._should_skip_shim_creation(shim_path):
            return
        if self._has_non_symlink_collision(shim_path):
            msg = f"{shim_path} already exists and is not a symlink"
            raise FileExistsError(msg)
        create_shim_symlinks(shim_path.parent, [name])

    def _get_replay_shim_path(self, name: str) -> Path | None:
        """Return the target shim path when replay shims are writable."""
        env = self.environment
        if env is None or env.shim_dir is None:
            return None
        return Path(env.shim_dir) / name

    def _should_skip_shim_creation(self, shim_path: Path) -> bool:
        """Return ``True`` when the shim already points to a valid target."""
        if not shim_path.is_symlink():
            return False
        return not self._is_broken_symlink(shim_path)

    def _has_non_symlink_collision(self, shim_path: Path) -> bool:
        """Return ``True`` when a non-symlink file blocks shim creation."""
        return shim_path.exists() and not shim_path.is_symlink()

    @staticmethod
    def _is_broken_symlink(path: Path) -> bool:
        """Return ``True`` when *path* is a symlink whose target is missing."""
        return path.is_symlink() and not path.exists()

    def _get_double(self, command_name: str, kind: DoubleKind) -> CommandDouble:
        dbl = self._doubles.get(command_name)
        if dbl is None:
            dbl = CommandDouble(command_name, self, kind)
            self._doubles[command_name] = dbl
            self.register_command(command_name)
        elif dbl.kind is not kind:
            msg = (
                f"{command_name!r} already registered as {dbl.kind}; "
                f"cannot register as {kind}"
            )
            raise ValueError(msg)
        return dbl

    def stub(self, command_name: str) -> CommandDouble:
        """Create or retrieve a stub for *command_name*."""
        return self._get_double(command_name, DoubleKind.STUB)

    def mock(self, command_name: str) -> CommandDouble:
        """Create or retrieve a mock for *command_name*."""
        return self._get_double(command_name, DoubleKind.MOCK)

    def spy(self, command_name: str) -> CommandDouble:
        """Create or retrieve a spy for *command_name*."""
        return self._get_double(command_name, DoubleKind.SPY)

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

        def _call_handler() -> Response:
            if double.handler is None:
                base = double.response
                # Clone to avoid mutating the shared static response instance
                return dc.replace(base, env=dict(base.env))

            return double.handler(invocation)

        env = double.expectation.env
        if env:
            with temporary_env(env):
                resp = _call_handler()
        else:
            resp = _call_handler()

        if env:
            resp.env.update(env)
        return resp

    def _make_response(self, invocation: Invocation) -> Response:
        double = self._doubles.get(invocation.command)
        if double is None:
            resp = Response(stdout=invocation.command)
        elif double.passthrough_mode:
            resp = self._prepare_passthrough(double, invocation)
        else:
            resp = self._handle_regular_invocation(double, invocation)
        invocation.apply(resp)
        return resp

    def _handle_regular_invocation(
        self, double: CommandDouble, invocation: Invocation
    ) -> Response:
        """Handle a non-passthrough invocation with optional recording."""
        resp = self._invoke_handler(double, invocation)
        if double.is_recording:
            double.invocations.append(invocation)
        return resp

    def _handle_invocation(self, invocation: Invocation) -> Response:
        """Record *invocation* and return the configured response."""
        resp = self._make_response(invocation)
        if resp.passthrough is None:
            self.journal.append(invocation)
        return resp

    def _prepare_passthrough(
        self, double: CommandDouble, invocation: Invocation
    ) -> Response:
        """Record passthrough intent and return instructions for the shim."""
        lookup_path = self.environment.original_environment.get(
            "PATH", os.environ.get("PATH", "")
        )
        return self._passthrough_coordinator.prepare_request(
            double, invocation, lookup_path, self._runner.timeout
        )

    def _handle_passthrough_result(self, result: PassthroughResult) -> Response:
        """Finalize a passthrough invocation once the shim reports results."""
        double, invocation, resp = self._passthrough_coordinator.finalize_result(result)
        if double.is_recording:
            double.invocations.append(invocation)
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
        self._server = CallbackIPCServer(
            self.environment.socket_path,
            self._handle_invocation,
            self._handle_passthrough_result,
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
