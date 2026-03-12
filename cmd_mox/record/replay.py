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
import typing as t

from cmd_mox.errors import LifecycleError, VerificationError

from .fixture import FixtureFile

if t.TYPE_CHECKING:
    from pathlib import Path

    from cmd_mox.ipc import Invocation, Response

    from .fixture import RecordedInvocation


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

    # -- Properties -----------------------------------------------------------

    @property
    def fixture_path(self) -> Path:
        """The path to the fixture JSON file."""
        return self._fixture_path

    @property
    def strict_matching(self) -> bool:
        """Whether strict matching mode is enabled."""
        return self._strict_matching

    @property
    def allow_unmatched(self) -> bool:
        """Whether unconsumed recordings are tolerated during verification."""
        return self._allow_unmatched

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
            If the fixture file has an incompatible schema version.
        """
        if self._fixture is not None:
            msg = "Fixture already loaded; load() may only be called once"
            raise LifecycleError(msg)
        self._fixture = FixtureFile.load(self._fixture_path)

    def _ensure_loaded(self) -> FixtureFile:
        """Return the loaded fixture, raising if not yet loaded."""
        if self._fixture is None:
            msg = "Fixture not loaded; call load() first"
            raise LifecycleError(msg)
        return self._fixture

    # -- Matching (private) ---------------------------------------------------

    def _matches_strict(
        self,
        invocation: Invocation,
        recording: RecordedInvocation,
    ) -> bool:
        """Check strict-mode match: command + args + stdin + env_subset."""
        if invocation.command != recording.command:
            return False
        if invocation.args != recording.args:
            return False
        if invocation.stdin != recording.stdin:
            return False
        # Subset containment: every key-value in env_subset must appear
        # in the invocation env.  Extra keys in the invocation are fine.
        for key, value in recording.env_subset.items():
            if invocation.env.get(key) != value:
                return False
        return True

    def _matches_fuzzy(
        self,
        invocation: Invocation,
        recording: RecordedInvocation,
    ) -> bool:
        """Check fuzzy-mode match: command + args only."""
        if invocation.command != recording.command:
            return False
        return invocation.args == recording.args

    # -- Public API -----------------------------------------------------------

    def match(self, invocation: Invocation) -> Response | None:
        """Find the first unconsumed recording matching *invocation*.

        When a match is found, the recording is marked as consumed and
        a ``Response`` is returned.  When no match is found, returns
        ``None``.

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
            If the fixture has not been loaded.
        """
        fixture = self._ensure_loaded()
        matcher = self._matches_strict if self._strict_matching else self._matches_fuzzy

        with self._lock:
            for idx, recording in enumerate(fixture.recordings):
                if idx in self._consumed:
                    continue
                if matcher(invocation, recording):
                    self._consumed.add(idx)
                    from cmd_mox.ipc import Response as _Response

                    return _Response(
                        stdout=recording.stdout,
                        stderr=recording.stderr,
                        exit_code=recording.exit_code,
                    )
        return None

    def verify_all_consumed(self) -> None:
        """Verify that all recordings were consumed during replay.

        Raises
        ------
        LifecycleError
            If the fixture has not been loaded.
        VerificationError
            If any recordings were not consumed and
            ``allow_unmatched`` is ``False``.
        """
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
