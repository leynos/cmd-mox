"""RecordingSession: manages capture of passthrough invocations to fixtures.

A ``RecordingSession`` collects ``(Invocation, Response)`` pairs from
passthrough spy executions, filters environment variables to a safe subset,
and persists the results as a versioned JSON fixture file.

Lifecycle: ``start()`` -> ``record()`` (one or more) -> ``finalize()``.
"""

from __future__ import annotations

import dataclasses as dc
import datetime as dt
import threading
import typing as typ
from pathlib import Path

from cmd_mox.errors import LifecycleError

from .env_filter import filter_env_subset
from .fixture import FixtureFile, FixtureMetadata, RecordedInvocation

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    from cmd_mox.ipc import Invocation, Response

    from .scrubber import Scrubber


def _normalize_command_filter(command_filter: str | list[str] | None) -> list[str]:
    """Normalize a command filter to case-folded entries.

    Returns
    -------
    list[str]
        A fresh list with every entry normalized.  ``None`` and an empty
        input list both produce an empty list, which disables filtering.
        Copying the input keeps later caller mutations from changing what
        this session records.
    """
    entries = [command_filter] if isinstance(command_filter, str) else command_filter
    return [entry.lower() for entry in entries or []]


class RecordingSession:
    """Capture passthrough invocations and persist them as a fixture file.

    Parameters
    ----------
    fixture_path : Path | str
        Destination path for the fixture JSON file.
    scrubber : Scrubber | None
        Optional scrubber for sanitizing recordings before persistence.
    env_allowlist : list[str] | None
        Additional environment variable keys to include when present.
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
        """Configure the fixture destination, scrubber, env allowlist, and filter."""
        self._fixture_path = Path(fixture_path)
        self._scrubber = scrubber
        self._env_allowlist: list[str] = list(env_allowlist or [])
        self._command_filter: list[str] = _normalize_command_filter(command_filter)

        self._recordings: list[RecordedInvocation] = []
        self._lock = threading.Lock()
        # Serializes the whole finalize sequence.  _lock stays reserved for
        # short state transitions so file I/O never holds it.
        self._finalize_lock = threading.Lock()
        self._started_at: dt.datetime | None = None
        self._finalized: bool = False
        self._fixture_file: FixtureFile | None = None

    @property
    def fixture_path(self) -> Path:
        """The destination path for the fixture JSON file."""
        return self._fixture_path

    @property
    def is_started(self) -> bool:
        """Whether the session has been started."""
        return self._started_at is not None

    def start(self) -> None:
        """Begin the recording session.

        Raises
        ------
        LifecycleError
            If the session has already been finalized.
        """
        # The finalized check and the timestamp assignment share one critical
        # section so a concurrent finalize() cannot interleave between them.
        with self._lock:
            if self._finalized:
                msg = "Cannot start a finalized recording session"
                raise LifecycleError(msg)
            self._started_at = dt.datetime.now(dt.UTC)

    def _validate_record_preconditions(self, duration_ms: int) -> None:
        """Validate preconditions before recording an invocation.

        Must be called while holding ``self._lock`` so the ``_finalized``
        check and the subsequent state snapshot cannot race a finalize().

        Parameters
        ----------
        duration_ms : int
            Wall-clock execution time in milliseconds to validate.

        Raises
        ------
        LifecycleError
            If the session has not been started or has been finalized.
        ValueError
            If ``duration_ms`` is negative.
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

    def _should_record_command(self, command: str) -> bool:
        """Return whether *command* passes the configured command filter."""
        # An absent or empty filter means "record everything"; an empty string
        # entry is still a real filter that matches only the empty command.
        if len(self._command_filter) == 0:
            return True
        return command.lower() in self._command_filter

    def record(
        self,
        invocation: Invocation,
        response: Response,
        *,
        duration_ms: int = 0,
        explicit_keys: cabc.Iterable[str] | None = None,
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
        explicit_keys : collections.abc.Iterable[str] | None
            Keys requested through ``.with_env()`` for this request.  They
            override the system-key and sensitive-name exclusions, but never
            the CmdMox-internal exclusion, and are recorded only when present
            in ``invocation.env``.

        Raises
        ------
        LifecycleError
            If the session has not been started or has been finalized.
        ValueError
            If ``duration_ms`` is negative.

        """  # ruff: ignore[docstring-extraneous-exception] - _validate_record_preconditions propagates these caller-visible failures.
        # Build under _lock so a concurrent finalize() cannot snapshot
        # _recordings between the lifecycle check and the build.
        with self._lock:
            self._validate_record_preconditions(duration_ms)

            if not self._should_record_command(invocation.command):
                return

            env_subset = filter_env_subset(
                invocation.env,
                command=invocation.command,
                allowlist=self._env_allowlist,
                explicit_keys=explicit_keys,
            )

            recording = RecordedInvocation(
                # Provisional only: the append-order sequence is assigned once
                # the scrubber has returned.
                sequence=-1,
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

        # Scrub outside _lock so a scrubber may call back into the session
        # without deadlocking and so independent scrubs can run concurrently.
        if self._scrubber is not None:
            recording = self._scrubber.scrub(recording)

        with self._lock:
            # Finalization may have run while the scrubber held the recording.
            if self._finalized:
                msg = "Cannot record after the session has been finalized"
                raise LifecycleError(msg)
            recording = dc.replace(recording, sequence=len(self._recordings))
            self._recordings.append(recording)

    def finalize(self) -> FixtureFile:
        """Finalize the session and persist the fixture to disk.

        The session must have been started.  Concurrent calls are serialized:
        exactly one fixture is built and written, and every caller receives
        that same object.  If the write fails, the session remains finalized
        but no fixture is cached, so a later call retries the save.

        Returns
        -------
        FixtureFile
            The assembled fixture file that was persisted.

        Raises
        ------
        LifecycleError
            If the session has not been started.
        cmd_mox.atomic_write.AtomicWriteError
            If the fixture cannot be written.  A previous complete fixture at
            the destination is left in place when replacement did not occur.
        """  # ruff: ignore[docstring-extraneous-exception] - FixtureFile.save propagates this caller-visible failure.
        # Hold the finalize lock across the cache check, snapshot, write, and
        # cache assignment.  _lock is taken only while reading or updating
        # in-memory state, so file I/O never blocks record() or start().
        with self._finalize_lock:
            if self._fixture_file is not None:
                return self._fixture_file

            with self._lock:
                if self._started_at is None:
                    msg = "Recording session has not been started; call start() first"
                    raise LifecycleError(msg)
                self._finalized = True
                fixture = FixtureFile(
                    version=FixtureFile.SCHEMA_VERSION,
                    metadata=FixtureMetadata.create(),
                    recordings=list(self._recordings),
                    scrubbing_rules=[],
                )

            fixture.save(self._fixture_path)
            self._fixture_file = fixture
            return fixture
