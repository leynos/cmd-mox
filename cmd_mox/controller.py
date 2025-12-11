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
from .environment import (
    IS_WINDOWS,
    EnvironmentManager,
    ensure_dir_exists,
    temporary_env,
)
from .errors import LifecycleError, MissingEnvironmentError, UnexpectedCommandError
from .ipc import (
    CallbackIPCServer,
    CallbackNamedPipeServer,
    Invocation,
    PassthroughResult,
    Response,
)
from .passthrough import PassthroughConfig, PassthroughCoordinator
from .shimgen import create_shim_symlinks
from .test_doubles import CommandDouble, DoubleKind
from .verifiers import CountVerifier, OrderVerifier, UnexpectedCommandVerifier

if t.TYPE_CHECKING:
    from .expectations import Expectation


_ENV_ATTR_RULES: dict[str, tuple[str, bool]] = {
    "shim_dir": ("Replay shim directory", True),
    "socket_path": ("Replay socket path", False),
}


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
        self._server: CallbackIPCServer | CallbackNamedPipeServer | None = None
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
        except BaseException:
            # ``KeyboardInterrupt`` and friends should not leak shims or PATH
            # mutations. Clean up before re-raising so the caller sees the
            # original failure.
            self._cleanup_after_replay_error()
            raise
        self._phase = Phase.REPLAY

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
    def _execute_handler(
        self,
        double: CommandDouble,
        invocation: Invocation,
        overrides: dict[str, str],
    ) -> Response:
        """Execute the handler with the appropriate environment context."""
        if double.handler is None:
            base = double.response
            return dc.replace(base, env=dict(base.env))
        if overrides:
            with temporary_env(overrides):
                return double.handler(invocation)
        return double.handler(invocation)

    def _finalize_response_env(self, resp: Response, overrides: dict[str, str]) -> None:
        """Ensure response environment includes all expectation overrides."""
        if not overrides:
            return
        # Ensure the shim observes the injected variables even when the handler
        # returns a cached Response instance, without clobbering handler-set
        # overrides.
        for key, value in overrides.items():
            resp.env.setdefault(key, value)

    def _invoke_handler(
        self, double: CommandDouble, invocation: Invocation
    ) -> Response:
        """Run ``double``'s handler within its expectation environment."""
        overrides = self._apply_expectation_env(double, invocation)
        resp = self._execute_handler(double, invocation, overrides)
        self._finalize_response_env(resp, overrides)
        return resp

    def _apply_expectation_env(
        self, double: CommandDouble, invocation: Invocation
    ) -> dict[str, str]:
        """Validate and apply expectation environment to invocation.

        Returns
        -------
        dict[str, str]
            The environment overrides that were applied.

        Raises
        ------
        UnexpectedCommandError
            When the expectation environment conflicts with invocation environment.
        """
        expectation_env = double.expectation.env or {}
        overrides = dict(expectation_env)

        if not overrides:
            return overrides

        conflicts = {
            key: invocation.env[key]
            for key, value in overrides.items()
            if key in invocation.env and invocation.env[key] != value
        }

        if conflicts:
            conflict_list = ", ".join(f"{k}={v!r}" for k, v in conflicts.items())
            msg = (
                f"Invocation for {invocation.command!r} provided conflicting "
                f"environment values: {conflict_list}"
            )
            raise UnexpectedCommandError(msg)

        invocation.env.update(overrides)
        return overrides

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
        """Record passthrough intent and return instructions for the shim.

        This method prepares two separate values: ``extra_env`` (expectation
        overrides from :meth:`_apply_expectation_env`) and ``lookup_path``
        (the original PATH for command resolution). These are passed to
        :class:`~cmd_mox.passthrough.PassthroughCoordinator` separately; the
        final merging into an effective execution environment happens
        downstream when the shim consumes the resulting
        :class:`~cmd_mox.ipc.PassthroughRequest`.
        """
        overrides = self._apply_expectation_env(double, invocation)
        lookup_path = self.environment.original_environment.get(
            "PATH", os.environ.get("PATH", "")
        )
        config = PassthroughConfig(
            lookup_path=lookup_path,
            timeout=self._runner.timeout,
            extra_env=overrides or None,
        )
        return self._passthrough_coordinator.prepare_request(
            double,
            invocation,
            config,
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
            raise MissingEnvironmentError(MissingEnvironmentError.DEFAULT_MESSAGE)

        missing: list[str] = []
        for attr in attrs:
            error = self._validate_env_attr(env, attr)
            if error is not None:
                missing.append(error)

        if missing:
            raise MissingEnvironmentError("; ".join(missing))

    def _validate_env_attr(self, env: EnvironmentManager, attr: str) -> str | None:
        """Return an error message when *attr* is invalid, otherwise ``None``."""
        label, requires_dir = _ENV_ATTR_RULES.get(
            attr, (f"Replay {attr.replace('_', ' ')}", False)
        )
        value = getattr(env, attr, None)

        if requires_dir:
            try:
                ensure_dir_exists(
                    value,
                    name=label,
                    error_type=MissingEnvironmentError,
                    missing_message=f"{label} is missing",
                )
            except MissingEnvironmentError as exc:
                return str(exc)
            return None

        if value is None:
            return f"{label} is missing"

        return None

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

    def _validate_replay_environment(self) -> tuple[Path, Path]:
        """Ensure the replay environment is fully prepared.

        Returns
        -------
        tuple[Path, Path]
            A tuple containing the shim directory and socket path.

        Raises
        ------
        MissingEnvironmentError
            If the environment manager or required paths are missing.
        """
        env = self.environment
        if env is None:
            raise MissingEnvironmentError(MissingEnvironmentError.DEFAULT_MESSAGE)

        shim_dir = ensure_dir_exists(
            env.shim_dir,
            name="Replay shim directory",
            error_type=MissingEnvironmentError,
        )
        if env.socket_path is None:
            msg = "Replay socket path is missing"
            raise MissingEnvironmentError(msg)
        return shim_dir, Path(env.socket_path)

    def _is_environment_initialized(self) -> bool:
        """Check if environment manager is properly initialized."""
        env = self.environment
        return (
            env is not None and env.shim_dir is not None and env.socket_path is not None
        )

    def _start_ipc_server(self) -> None:
        """Prepare shims and launch the IPC server."""
        self.journal.clear()
        self._commands = self._registered_commands() | self._commands
        if not self._entered and not self._is_environment_initialized():
            msg = "Environment manager not initialised"
            raise MissingEnvironmentError(msg)
        shim_dir, socket_path = self._validate_replay_environment()
        create_shim_symlinks(shim_dir, self._commands)
        server_factory = CallbackNamedPipeServer if IS_WINDOWS else CallbackIPCServer
        self._server = server_factory(
            socket_path,
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
