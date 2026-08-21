"""ReplaySession: replays recorded fixtures as mock responses.

A ``ReplaySession`` loads a previously recorded JSON fixture file, matches
incoming command invocations against the recorded entries, and tracks which
recordings have been consumed.  Two matching modes are supported:

- **Strict:** command, args, stdin, and env_subset must all match.
- **Fuzzy:** only command and args must match; stdin and env are ignored.

Lifecycle: ``__init__()`` -> ``load()`` -> ``match()`` (zero or more) ->
``verify_all_consumed()``.
"""

from __future__ import annotations

import threading
import typing as typ

from cmd_mox.errors import LifecycleError, VerificationError

from .fixture import FixtureFile
from .matching import InvocationMatcher

if typ.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox.ipc import Invocation, Response


class ReplaySession:
    """Replay recorded fixtures as mock responses.

    Parameters
    ----------
    fixture_path : Path | str
        Path to the JSON fixture file to load.
    strict_matching : bool
        When ``True`` (default), matching requires command, args, stdin,
        and env_subset to match.  When ``False``, only command and args
        must match.
    allow_unmatched : bool
        When ``True``, ``verify_all_consumed()`` does not raise even if
        some recordings were not consumed.  Defaults to ``False``.
    """

    def __init__(
        self,
        fixture_path: Path | str,
        *,
        strict_matching: bool = True,
        allow_unmatched: bool = False,
    ) -> None:
        from pathlib import Path as _Path

        self._fixture_path = _Path(fixture_path)
        self._strict_matching = strict_matching
        self._allow_unmatched = allow_unmatched
        self._fixture: FixtureFile | None = None
        self._consumed: set[int] = set()
        self._lock = threading.Lock()
        self._matcher = InvocationMatcher(strict=strict_matching)

    # -- Properties -----------------------------------------------------------

    @property
    def fixture_path(self) -> Path:
        """Return the path to the fixture JSON file.

        Returns
        -------
        pathlib.Path
            Fixture path configured for this session.
        """
        return self._fixture_path

    @property
    def strict_matching(self) -> bool:
        """Return whether strict matching mode is enabled.

        Returns
        -------
        bool
            ``True`` when stdin and environment must also match.
        """
        return self._strict_matching

    @property
    def allow_unmatched(self) -> bool:
        """Return whether unconsumed recordings are tolerated.

        Returns
        -------
        bool
            ``True`` when verification permits unmatched recordings.
        """
        return self._allow_unmatched

    @property
    def consumed_count(self) -> int:
        """Return the number of recordings consumed so far.

        Returns
        -------
        int
            Number of consumed fixture recordings.
        """
        with self._lock:
            return len(self._consumed)

    def is_consumed(self, index: int) -> bool:
        """Check whether the recording at *index* has been consumed.

        Parameters
        ----------
        index : int
            The zero-based index of the recording in the fixture.

        Returns
        -------
        bool
            ``True`` if the recording at *index* has been consumed.
        """
        with self._lock:
            return index in self._consumed

    # -- Lifecycle ------------------------------------------------------------

    def load(self) -> None:
        """Load and parse the fixture file with schema validation.

        Raises
        ------
        LifecycleError
            If the fixture has already been loaded.
        FileNotFoundError
            If the fixture file does not exist.
        ValueError
            If the fixture is not valid JSON or fails schema validation.
        """  # noqa: DOC502 - FixtureFile.load propagates these caller-visible failures.
        if self._fixture is not None:
            msg = "Fixture already loaded; load() may only be called once"
            raise LifecycleError(msg)
        self._fixture = FixtureFile.load(self._fixture_path)

    def _ensure_loaded(self) -> FixtureFile:
        """Return the loaded fixture, raising if not yet loaded."""  # noqa: DOC201, DOC501 - private load guard has an obvious FixtureFile return and raises only LifecycleError
        if self._fixture is None:
            msg = "Fixture not loaded; call load() first"
            raise LifecycleError(msg)
        return self._fixture

    # -- Public API -----------------------------------------------------------

    def match(self, invocation: Invocation) -> Response | None:
        """Find the best-fit unconsumed recording matching *invocation*.

        When a match is found, the recording is marked as consumed and
        a ``Response`` is returned.  When no match is found, returns
        ``None``.

        The matcher uses a lexicographic scoring approach to select the
        most appropriate recording when multiple candidates qualify.

        Parameters
        ----------
        invocation : Invocation
            The incoming command invocation to match.

        Returns
        -------
        Response | None
            A ``Response`` built from the matched recording, or ``None``.

        Raises
        ------
        LifecycleError
            If :meth:`load` has not been called successfully.

        """  # noqa: DOC502 - _ensure_loaded propagates this caller-visible failure.
        fixture = self._ensure_loaded()

        with self._lock:
            idx = self._matcher.find_match(
                invocation, fixture.recordings, self._consumed
            )

            if idx is None:
                return None

            self._consumed.add(idx)
            recording = fixture.recordings[idx]

            from cmd_mox.ipc import Response as _Response

            return _Response(
                stdout=recording.stdout,
                stderr=recording.stderr,
                exit_code=recording.exit_code,
            )

    def verify_all_consumed(self) -> None:
        """Verify that all recordings were consumed during replay.

        Raises
        ------
        LifecycleError
            If :meth:`load` has not been called successfully.
        VerificationError
            If any recordings were not consumed and
            ``allow_unmatched`` is ``False``.
        """  # noqa: DOC502 - _ensure_loaded propagates this caller-visible failure.
        fixture = self._ensure_loaded()

        if self._allow_unmatched:
            return

        with self._lock:
            unconsumed_indices = set(range(len(fixture.recordings))) - self._consumed

            if not unconsumed_indices:
                return

            details: list[str] = []
            for idx in sorted(unconsumed_indices):
                rec = fixture.recordings[idx]
                details.append(f"  [{idx}] {rec.command} {' '.join(rec.args)}")

            msg = (
                f"Not all fixture recordings were consumed during replay "
                f"({len(unconsumed_indices)} unconsumed):\n" + "\n".join(details)
            )
            raise VerificationError(msg)
