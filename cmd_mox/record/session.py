"""RecordingSession: manages capture of passthrough invocations to fixtures.

A ``RecordingSession`` collects ``(Invocation, Response)`` pairs from
passthrough spy executions, filters environment variables to a safe subset,
and persists the results as a versioned JSON fixture file.

Lifecycle: ``start()`` -> ``record()`` (one or more) -> ``finalize()``.
"""

from __future__ import annotations

import datetime as dt
import threading
import typing as t
from pathlib import Path

from cmd_mox.errors import LifecycleError

from .env_filter import filter_env_subset
from .fixture import FixtureFile, FixtureMetadata, RecordedInvocation

if t.TYPE_CHECKING:
    from cmd_mox.ipc import Invocation, Response

    from .scrubber import Scrubber


class RecordingSession:
    """Capture passthrough invocations and persist them as a fixture file.

    Parameters
    ----------
    fixture_path : Path | str
        Destination path for the fixture JSON file.
    scrubber : Scrubber | None
        Optional scrubber for sanitizing recordings before persistence.
    env_allowlist : list[str] | None
        Additional environment variable keys to always include.
    command_filter : str | list[str] | None
        If set, only record invocations matching the given command name(s).
    """

    def __init__(
        self,
        fixture_path: Path | str,
        *,
        scrubber: Scrubber | None = None,
        env_allowlist: list[str] | None = None,
        command_filter: str | list[str] | None = None,
    ) -> None:
        self._fixture_path = Path(fixture_path)
        self._scrubber = scrubber
        self._env_allowlist: list[str] = list(env_allowlist or [])
        self._command_filter: list[str] | None = (
            [command_filter]
            if isinstance(command_filter, str)
            else list(command_filter)
            if command_filter is not None
            else None
        )

        self._recordings: list[RecordedInvocation] = []
        self._lock = threading.Lock()
        self._started_at: dt.datetime | None = None
        self._finalized: bool = False
        self._fixture_file: FixtureFile | None = None

    @property
    def fixture_path(self) -> Path:
        """The destination path for the fixture JSON file."""
        return self._fixture_path

    @property
    def is_started(self) -> bool:
        """Return ``True`` if the session has been started."""
        return self._started_at is not None

    def start(self) -> None:
        """Begin the recording session.

        Raises
        ------
        LifecycleError
            If the session has already been finalized.
        """
        if self._finalized:
            msg = "Cannot start a finalized recording session"
            raise LifecycleError(msg)
        self._started_at = dt.datetime.now(dt.UTC)

    def _validate_record_preconditions(self, duration_ms: int) -> None:
        """Validate preconditions before recording an invocation.

        Parameters
        ----------
        duration_ms : int
            Wall-clock execution time in milliseconds to validate.

        Raises
        ------
        LifecycleError
            If the session has not been started or has been finalized.
        ValueError
            If *duration_ms* is negative.
        """
        if self._started_at is None:
            msg = "Recording session has not been started; call start() first"
            raise LifecycleError(msg)
        if self._finalized:
            msg = "Cannot record after the session has been finalized"
            raise LifecycleError(msg)
        if duration_ms < 0:
            msg = f"duration_ms must be non-negative, got {duration_ms}"
            raise ValueError(msg)

    def record(
        self,
        invocation: Invocation,
        response: Response,
        *,
        duration_ms: int = 0,
    ) -> None:
        """Record a single passthrough invocation.

        Parameters
        ----------
        invocation : Invocation
            The captured command invocation.
        response : Response
            The response from the real command execution.
        duration_ms : int
            Wall-clock execution time in milliseconds (default ``0``).

        Raises
        ------
        LifecycleError
            If the session has not been started or has been finalized.
        ValueError
            If *duration_ms* is negative.
        """
        self._validate_record_preconditions(duration_ms)

        # Skip if command filter is set and this command is not in it.
        if self._command_filter and invocation.command not in self._command_filter:
            return

        env_subset = filter_env_subset(
            invocation.env,
            command=invocation.command,
            allowlist=self._env_allowlist,
        )

        # Sequence assignment and list append must be atomic so that
        # concurrent passthrough completions on different IPC threads
        # produce correct, gap-free sequence numbers.
        with self._lock:
            recording = RecordedInvocation(
                sequence=len(self._recordings),
                command=invocation.command,
                args=list(invocation.args),
                stdin=invocation.stdin,
                env_subset=env_subset,
                stdout=response.stdout,
                stderr=response.stderr,
                exit_code=response.exit_code,
                timestamp=dt.datetime.now(dt.UTC).isoformat(),
                duration_ms=duration_ms,
            )

            if self._scrubber is not None:
                recording = self._scrubber.scrub(recording)

            self._recordings.append(recording)

    def finalize(self) -> FixtureFile:
        """Finalize the session and persist the fixture to disk.

        Idempotent: calling ``finalize()`` a second time returns the same
        ``FixtureFile`` without re-writing the file.

        Returns
        -------
        FixtureFile
            The assembled fixture file that was persisted.
        """
        if self._fixture_file is not None:
            return self._fixture_file

        metadata = FixtureMetadata.create()
        fixture = FixtureFile(
            version=FixtureFile.SCHEMA_VERSION,
            metadata=metadata,
            recordings=list(self._recordings),
            scrubbing_rules=[],
        )
        fixture.save(self._fixture_path)
        self._finalized = True
        self._fixture_file = fixture
        return fixture
