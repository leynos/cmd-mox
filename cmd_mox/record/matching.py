"""InvocationMatcher: best-fit selection for replay invocations.

The ``InvocationMatcher`` class selects the best-fit recording from a list
of candidates based on command, args, stdin, and environment matching.
It supports both strict mode (all criteria must match) and fuzzy mode
(only command and args are required).

The matcher uses a lexicographic scoring tuple to deterministically select
the most appropriate recording when multiple candidates qualify.
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
        When ``True``, all matching criteria must be satisfied.
        When ``False``, only command and args are required.
    match_env : bool
        When ``True``, environment subset must match in strict mode.
    match_stdin : bool
        When ``True``, stdin must match in strict mode.
    """

    def __init__(
        self,
        *,
        strict: bool,
        match_env: bool,
        match_stdin: bool,
    ) -> None:
        self._strict = strict
        self._match_env = match_env
        self._match_stdin = match_stdin

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
        # Command and args are mandatory in all modes
        if invocation.command != recording.command:
            return False
        if invocation.args != recording.args:
            return False

        # In strict mode, stdin and env_subset are mandatory gates
        if self._strict:
            if self._match_stdin and invocation.stdin != recording.stdin:
                return False
            if self._match_env:
                # Subset containment: every key-value in env_subset must
                # appear in the invocation env
                for key, value in recording.env_subset.items():
                    if invocation.env.get(key) != value:
                        return False

        return True

    def _compute_score(
        self,
        invocation: Invocation,
        recording: RecordedInvocation,
    ) -> tuple[bool, int, int, int]:
        """Compute a lexicographic score for a recording.

        The score tuple is ordered by priority:
        1. Exact stdin match (True is better)
        2. Number of matching env pairs (higher is better)
        3. Size of env_subset (higher is better, indicates specificity)
        4. Negative of sequence (lower sequence is better for tie-breaking)

        Returns
        -------
        tuple[bool, int, int, int]
            A comparable score tuple (higher is better).
        """
        stdin_match = invocation.stdin == recording.stdin
        matching_env_pairs = sum(
            1
            for key, value in recording.env_subset.items()
            if invocation.env.get(key) == value
        )
        env_subset_size = len(recording.env_subset)
        # Use negative sequence for tie-breaking (lower sequence wins)
        sequence_tie_break = -recording.sequence

        return (stdin_match, matching_env_pairs, env_subset_size, sequence_tie_break)

    def find_match(
        self,
        invocation: Invocation,
        recordings: list[RecordedInvocation],
        consumed: set[int],
    ) -> int | None:
        """Find the best-fit recording index for an invocation.

        This method iterates the recordings in list order, skips consumed
        indices, filters by compatibility, computes scores, and returns
        the index with the highest score. On a score tie, the earlier
        index wins.

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
        best_score: tuple[bool, int, int, int] | None = None

        for idx, recording in enumerate(recordings):
            if idx in consumed:
                continue

            if not self.matches(invocation, recording):
                continue

            score = self._compute_score(invocation, recording)

            if best_score is None or score > best_score:
                best_idx = idx
                best_score = score

        return best_idx
