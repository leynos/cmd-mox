"""Property tests for InvocationMatcher selection and its mode relations.

Two obligations live here. MATCH-1 checks the matcher against an independent
oracle over generated scenarios, and proves the check can fail by running
seeded mutants over hand-built witnesses. MATCH-2 pins the relations between
strict and fuzzy compatibility.
"""

from __future__ import annotations

import collections.abc as cabc
import dataclasses as dc

import hypothesis
import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from cmd_mox.ipc import Invocation
from cmd_mox.record.fixture import RecordedInvocation
from cmd_mox.record.matching import InvocationMatcher
from cmd_mox.unittests._replay_helpers import (
    RecordedInvocationSpec,
    ReplayScenario,
    check_find_match,
    classify_scenario,
    make_invocation,
    make_recorded_invocation,
    replay_scenarios,
)

type _Stats = tuple[bool, int, int]
type _KeyFn = cabc.Callable[[_Stats, int, int], tuple[int, ...]]
type _FindMatch = cabc.Callable[
    [Invocation, list[RecordedInvocation], set[int]], int | None
]

# The suite runs under xdist with file I/O, so the default 200ms deadline is
# noise rather than a signal.
_PROPERTY_SETTINGS = settings(deadline=None)
_FIND_SETTINGS = settings(database=None, deadline=None)

_SELECTION_LABELS = (
    "no candidate",
    "single candidate",
    "won on stdin",
    "won on env pairs",
    "won on subset size",
    "won on sequence",
    "won on list order",
)


def _scenario(
    *,
    strict: bool,
    invocation: Invocation,
    recordings: cabc.Sequence[RecordedInvocation],
    consumed: frozenset[int] = frozenset(),
) -> ReplayScenario:
    """Build a scenario from explicit parts.

    Parameters
    ----------
    strict : bool
        Whether the matcher runs in strict mode.
    invocation : Invocation
        The live invocation to match.
    recordings : collections.abc.Sequence of RecordedInvocation
        The recordings, in fixture order.
    consumed : frozenset of int
        Indices already consumed.

    Returns
    -------
    ReplayScenario
        The assembled scenario.
    """
    return ReplayScenario(
        strict=strict,
        invocation=invocation,
        recordings=tuple(recordings),
        consumed=consumed,
    )


# -- MATCH-1: oracle agreement -----------------------------------------------


@given(replay_scenarios())
@_PROPERTY_SETTINGS
def test_find_match_agrees_with_the_oracle(scenario: ReplayScenario) -> None:
    """The matcher selects exactly the oracle's best-fit unconsumed index."""
    check_find_match(scenario)


# -- MATCH-1 non-vacuity: class witnesses ------------------------------------

_NO_CANDIDATE_WITNESS = _scenario(
    strict=True,
    invocation=make_invocation("aa", ["x"]),
    recordings=[make_recorded_invocation(RecordedInvocationSpec(command="bb"))],
)

_SINGLE_CANDIDATE_WITNESS = _scenario(
    strict=True,
    invocation=make_invocation(),
    recordings=[make_recorded_invocation()],
)

_STDIN_WITNESS = _scenario(
    strict=False,
    invocation=make_invocation(stdin="s"),
    recordings=[
        make_recorded_invocation(RecordedInvocationSpec(sequence=0, stdin="s")),
        make_recorded_invocation(RecordedInvocationSpec(sequence=1, stdin="")),
    ],
)

_ENV_PAIR_WITNESS = _scenario(
    strict=False,
    invocation=make_invocation(env={"K": "v"}),
    recordings=[
        make_recorded_invocation(
            RecordedInvocationSpec(sequence=0, env_subset={"K": "w"})
        ),
        make_recorded_invocation(
            RecordedInvocationSpec(sequence=1, env_subset={"K": "v"})
        ),
    ],
)

_SUBSET_SIZE_WITNESS = _scenario(
    strict=False,
    invocation=make_invocation(env={"K": "v"}),
    recordings=[
        make_recorded_invocation(
            RecordedInvocationSpec(sequence=0, env_subset={"K": "v"})
        ),
        make_recorded_invocation(
            RecordedInvocationSpec(sequence=1, env_subset={"K": "v", "L": "w"})
        ),
    ],
)

_SEQUENCE_WITNESS = _scenario(
    strict=True,
    invocation=make_invocation(),
    recordings=[
        make_recorded_invocation(RecordedInvocationSpec(sequence=5)),
        make_recorded_invocation(RecordedInvocationSpec(sequence=1)),
    ],
)

_LIST_ORDER_WITNESS = _scenario(
    strict=True,
    invocation=make_invocation(),
    recordings=[
        make_recorded_invocation(RecordedInvocationSpec(sequence=1)),
        make_recorded_invocation(RecordedInvocationSpec(sequence=1)),
    ],
)

_WITNESSES = (
    ("no candidate", _NO_CANDIDATE_WITNESS),
    ("single candidate", _SINGLE_CANDIDATE_WITNESS),
    ("won on stdin", _STDIN_WITNESS),
    ("won on env pairs", _ENV_PAIR_WITNESS),
    ("won on subset size", _SUBSET_SIZE_WITNESS),
    ("won on sequence", _SEQUENCE_WITNESS),
    ("won on list order", _LIST_ORDER_WITNESS),
)


@pytest.mark.parametrize(
    ("label", "scenario"),
    _WITNESSES,
    ids=[label for label, _ in _WITNESSES],
)
def test_class_witness_carries_its_label_and_agrees_with_the_oracle(
    label: str,
    scenario: ReplayScenario,
) -> None:
    """A hand-built witness exercises its class and passes the oracle check."""
    assert classify_scenario(scenario) == label
    check_find_match(scenario)


@pytest.mark.parametrize("label", _SELECTION_LABELS)
def test_every_selection_class_is_reachable_from_the_strategy(label: str) -> None:
    """The scenario strategy can produce each selection class."""
    found = hypothesis.find(
        replay_scenarios(),
        lambda candidate: classify_scenario(candidate) == label,
        settings=_FIND_SETTINGS,
    )

    assert classify_scenario(found) == label


# -- MATCH-1 non-vacuity: seeded mutants -------------------------------------


def _oracle_key(stats: _Stats, sequence: int, index: int) -> tuple[int, ...]:
    """Rank like the design: stats, then lower sequence, then lower index."""
    return (int(stats[0]), stats[1], stats[2], -sequence, -index)


def _index_key(stats: _Stats, sequence: int, index: int) -> tuple[int, ...]:
    """Break stats ties by preferring the earlier list position."""
    del sequence
    return (int(stats[0]), stats[1], stats[2], -index)


def _swapped_key(stats: _Stats, sequence: int, index: int) -> tuple[int, ...]:
    """Rank environment specificity above stdin exactness."""
    return (stats[1], int(stats[0]), stats[2], -sequence, -index)


def _make_mutant(
    *,
    strict: bool,
    key: _KeyFn,
    honour_consumed: bool = True,
) -> _FindMatch:
    """Return a deliberately broken selection function ranked by *key*.

    Parameters
    ----------
    strict : bool
        Whether the mutant matcher runs in strict mode.
    key : collections.abc.Callable
        Ranking function applied to ``(stats, sequence, index)``.
    honour_consumed : bool
        When ``False``, the mutant ignores the consumed set.

    Returns
    -------
    collections.abc.Callable
        A ``find_match``-shaped function with one seeded defect.
    """
    matcher = InvocationMatcher(strict=strict)

    def find_match(
        invocation: Invocation,
        recordings: list[RecordedInvocation],
        consumed: set[int],
    ) -> int | None:
        """Select an index with the mutant's ranking rule."""
        best_index: int | None = None
        best_key: tuple[int, ...] | None = None
        for index, recording in enumerate(recordings):
            if honour_consumed and index in consumed:
                continue
            if not matcher.matches(invocation, recording):
                continue
            stats = matcher._env_match_stats(invocation, recording)
            candidate = key(stats, recording.sequence, index)
            if best_key is None or candidate > best_key:
                best_index, best_key = index, candidate
        return best_index

    return find_match


#: Equal stats, with the lower sequence later in the list.
_INDEX_MUTANT_WITNESS = _scenario(
    strict=True,
    invocation=make_invocation(),
    recordings=[
        make_recorded_invocation(RecordedInvocationSpec(sequence=5)),
        make_recorded_invocation(RecordedInvocationSpec(sequence=1)),
    ],
)

#: The best-fitting candidate is already consumed.
_CONSUMED_MUTANT_WITNESS = _scenario(
    strict=True,
    invocation=make_invocation(env={"K": "v"}),
    recordings=[
        make_recorded_invocation(
            RecordedInvocationSpec(sequence=0, env_subset={"K": "v"})
        ),
        make_recorded_invocation(RecordedInvocationSpec(sequence=1, env_subset={})),
    ],
    consumed=frozenset({0}),
)

#: One candidate matches stdin only; another matches more env pairs only.
_SWAPPED_MUTANT_WITNESS = _scenario(
    strict=False,
    invocation=make_invocation(stdin="s", env={"K": "v"}),
    recordings=[
        make_recorded_invocation(
            RecordedInvocationSpec(sequence=0, stdin="s", env_subset={})
        ),
        make_recorded_invocation(
            RecordedInvocationSpec(
                sequence=1, stdin="", env_subset={"K": "v", "L": "w"}
            )
        ),
    ],
)


def test_oracle_key_passes_every_mutant_witness() -> None:
    """The witnesses are solvable, so a mutant failure is the mutant's fault."""
    for witness in (
        _INDEX_MUTANT_WITNESS,
        _CONSUMED_MUTANT_WITNESS,
        _SWAPPED_MUTANT_WITNESS,
    ):
        check_find_match(witness)


def test_index_tie_break_mutant_is_rejected() -> None:
    """The check rejects a selection that prefers list order over sequence."""
    mutant = _make_mutant(strict=True, key=_index_key)

    with pytest.raises(AssertionError, match="find_match selected"):
        check_find_match(_INDEX_MUTANT_WITNESS, mutant)


def test_consumed_ignoring_mutant_is_rejected() -> None:
    """The check rejects a selection that ignores the consumed set."""
    mutant = _make_mutant(strict=True, key=_oracle_key, honour_consumed=False)

    with pytest.raises(AssertionError, match="find_match selected"):
        check_find_match(_CONSUMED_MUTANT_WITNESS, mutant)


def test_mutation_mutant_is_rejected() -> None:
    """The check rejects a selection that mutates the recordings it reads."""
    scenario = _INDEX_MUTANT_WITNESS
    honest = InvocationMatcher(strict=scenario.strict)

    def mutating_find_match(
        invocation: Invocation,
        recordings: list[RecordedInvocation],
        consumed: set[int],
    ) -> int | None:
        """Select correctly, then scribble on the first recording."""
        if recordings:
            recordings[0].args.append("mutated")
        return honest.find_match(invocation, recordings, consumed)

    with pytest.raises(AssertionError, match="mutated its inputs"):
        check_find_match(scenario, mutating_find_match)


def test_swapped_component_mutant_is_rejected() -> None:
    """The check rejects a selection that ranks env pairs above stdin."""
    mutant = _make_mutant(strict=False, key=_swapped_key)

    with pytest.raises(AssertionError, match="find_match selected"):
        check_find_match(_SWAPPED_MUTANT_WITNESS, mutant)


# -- MATCH-2: mode relations -------------------------------------------------


@given(replay_scenarios())
@_PROPERTY_SETTINGS
def test_strict_compatibility_implies_fuzzy_compatibility(
    scenario: ReplayScenario,
) -> None:
    """Anything a strict matcher accepts a fuzzy matcher accepts too."""
    strict = InvocationMatcher(strict=True)
    fuzzy = InvocationMatcher(strict=False)

    for recording in scenario.recordings:
        if strict.matches(scenario.invocation, recording):
            assert fuzzy.matches(scenario.invocation, recording)


_EXTRA_ENV = st.dictionaries(
    st.sampled_from(("K", "L", "M", "N")),
    st.sampled_from(("v", "w")),
    max_size=3,
)


def _absent_extra_keys(
    scenario: ReplayScenario,
    extra: cabc.Mapping[str, str],
) -> dict[str, str]:
    """Keep only *extra* keys no recording constrains, so none is overridden."""
    used = {key for recording in scenario.recordings for key in recording.env_subset}
    return {key: value for key, value in extra.items() if key not in used}


@given(scenario=replay_scenarios(), extra=_EXTRA_ENV)
@_PROPERTY_SETTINGS
def test_adding_unconstrained_live_env_keys_never_removes_a_strict_match(
    scenario: ReplayScenario,
    extra: cabc.Mapping[str, str],
) -> None:
    """A wider live environment keeps every strict match it had."""
    strict = InvocationMatcher(strict=True)
    widened_env = {**scenario.invocation.env, **_absent_extra_keys(scenario, extra)}
    widened = dc.replace(scenario.invocation, env=widened_env)

    for recording in scenario.recordings:
        if strict.matches(scenario.invocation, recording):
            assert strict.matches(widened, recording)


@given(replay_scenarios())
@_PROPERTY_SETTINGS
def test_find_match_mutates_neither_consumed_nor_recordings(
    scenario: ReplayScenario,
) -> None:
    """Selection is a pure query over its inputs."""
    matcher = InvocationMatcher(strict=scenario.strict)
    recordings = list(scenario.recordings)
    consumed = set(scenario.consumed)

    matcher.find_match(scenario.invocation, recordings, consumed)

    assert recordings == list(scenario.recordings)
    assert consumed == set(scenario.consumed)
