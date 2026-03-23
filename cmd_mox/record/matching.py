"""InvocationMatcher: best-fit selection for replay invocations.

The ``InvocationMatcher`` class selects the best-fit recording from a list
of candidates based on command, args, stdin, and environment matching.
It supports both strict mode (all criteria must match) and fuzzy mode
(only command and args are required).

The matcher uses a lexicographic scoring tuple to deterministically select
the most appropriate recording when multiple candidates qualify.  When two
candidates score equally, the one with the lower ``sequence`` value wins.
"""

from __future__ import annotations

import typing as t

if t.TYPE_CHECKING:
    from cmd_mox.ipc import Invocation

    from .fixture import RecordedInvocation


class InvocationMatcher:
    """Select best-fit recordings for replay invocations.

    Parameters
    ----------
    strict : bool
        When ``True``, all matching criteria must be satisfied
        (command, args, stdin, and env_subset).
        When ``False``, only command and args are required.
    """

    def __init__(self, *, strict: bool) -> None:
        self._strict = strict

    def matches(
        self,
        invocation: Invocation,
        recording: RecordedInvocation,
    ) -> bool:
        """Check if a recording is compatible with an invocation.

        This is a pure boolean predicate that does not modify state.

        Parameters
        ----------
        invocation : Invocation
            The live invocation to match.
        recording : RecordedInvocation
            The recorded invocation to test.

        Returns
        -------
        bool
            ``True`` if the recording is compatible, ``False`` otherwise.
        """
        if invocation.command != recording.command:
            return False
        if invocation.args != recording.args:
            return False

        if not self._strict:
            return True

        # Strict mode: stdin and env_subset must also match
        if invocation.stdin != recording.stdin:
            return False

        return all(
            invocation.env.get(key) == value
            for key, value in recording.env_subset.items()
        )

    def _env_match_stats(
        self,
        invocation: Invocation,
        recording: RecordedInvocation,
    ) -> tuple[bool, int, int]:
        """Compute preference stats for a compatible recording.

        Returns a 3-tuple ordered by priority:

        1. Exact stdin match (``True`` is better)
        2. Number of matching env pairs (higher is better)
        3. Size of ``env_subset`` (higher indicates greater specificity)

        Returns
        -------
        tuple[bool, int, int]
            A comparable stats tuple (higher is better).
        """
        stdin_match = invocation.stdin == recording.stdin
        matching_env_pairs = sum(
            1
            for key, value in recording.env_subset.items()
            if invocation.env.get(key) == value
        )
        env_subset_size = len(recording.env_subset)
        return stdin_match, matching_env_pairs, env_subset_size

    def find_match(
        self,
        invocation: Invocation,
        recordings: list[RecordedInvocation],
        consumed: set[int],
    ) -> int | None:
        """Find the best-fit recording index for an invocation.

        This method iterates the recordings in list order, skips consumed
        indices, filters by compatibility, computes stats, and returns
        the index with the highest stats.  When stats are equal, the
        recording with the lower ``sequence`` value wins.

        Parameters
        ----------
        invocation : Invocation
            The live invocation to match.
        recordings : list[RecordedInvocation]
            The list of recorded invocations from the fixture.
        consumed : set[int]
            The set of already-consumed recording indices.

        Returns
        -------
        int | None
            The index of the best-fit recording, or ``None`` if no
            compatible recording exists.
        """
        best_idx: int | None = None
        best_stats: tuple[bool, int, int] | None = None
        best_sequence: int | None = None

        for idx, recording in enumerate(recordings):
            if idx in consumed:
                continue

            if not self.matches(invocation, recording):
                continue

            stats = self._env_match_stats(invocation, recording)

            if best_stats is None or stats > best_stats:
                best_idx = idx
                best_stats = stats
                best_sequence = recording.sequence
            elif stats == best_stats and recording.sequence < best_sequence:  # type: ignore[operator]
                best_idx = idx
                best_sequence = recording.sequence

        return best_idx
