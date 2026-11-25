"""Shared builders and Hypothesis strategies for the replay test modules.

Several test modules exercise replay from different angles, and each grew its
own copy of the same builders until the copies disagreed about defaults. This
module owns the single copy. It deliberately lacks the ``test_`` prefix so
pytest does not collect it, and it holds test scaffolding only: nothing here is
imported by library code.
"""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import hypothesis.strategies as st

from cmd_mox.ipc import Invocation
from cmd_mox.record.fixture import FixtureFile, FixtureMetadata, RecordedInvocation
from cmd_mox.record.matching import InvocationMatcher
from cmd_mox.record.scrubber import ScrubbingRule

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

#: Fixed timestamp so recorded values are byte-identical across runs.
_TIMESTAMP = "2026-01-15T10:30:00+00:00"

#: The order in which :func:`classify_scenario` reports the score components.
_SCORE_LABELS = ("won on stdin", "won on env pairs", "won on subset size")

# Small alphabets keep generated recordings mutually compatible often enough
# that every scoring class is reachable without a long search.
_SCENARIO_COMMANDS = ("aa", "bb")
_SCENARIO_ARGS = ("x", "y")
_SCENARIO_ENV_KEYS = ("K", "L")
_SCENARIO_ENV_VALUES = ("v", "w")
_SCENARIO_STDIN = ("", "s")

type _Stats = tuple[bool, int, int]
type _RankKey = tuple[_Stats, int, int]


@dc.dataclass(frozen=True, slots=True)
class RecordedInvocationSpec:
    """Optional overrides for building a :class:`RecordedInvocation`."""

    command: str = "git"
    args: tuple[str, ...] = ("status",)
    sequence: int = 0
    stdin: str = ""
    env_subset: cabc.Mapping[str, str] = dc.field(default_factory=dict)
    stdout: str = "ok\n"
    stderr: str = ""
    exit_code: int = 0


@dc.dataclass(frozen=True, slots=True)
class ReplayScenario:
    """One matching scenario: a live invocation against recorded entries.

    Attributes
    ----------
    strict : bool
        Whether the matcher runs in strict mode.
    invocation : Invocation
        The live invocation to match.
    recordings : tuple[RecordedInvocation, ...]
        The recordings, in fixture order.
    consumed : frozenset[int]
        Indices already consumed before the scenario starts.
    """

    strict: bool
    invocation: Invocation
    recordings: tuple[RecordedInvocation, ...]
    consumed: frozenset[int]


def make_recorded_invocation(
    spec: RecordedInvocationSpec | None = None,
) -> RecordedInvocation:
    """Build a recorded invocation from *spec*.

    Parameters
    ----------
    spec : RecordedInvocationSpec or None
        Field overrides; ``None`` selects every default.

    Returns
    -------
    RecordedInvocation
        A recording stamped with the fixed replay-test timestamp.
    """
    resolved = RecordedInvocationSpec() if spec is None else spec
    return RecordedInvocation(
        sequence=resolved.sequence,
        command=resolved.command,
        args=list(resolved.args),
        stdin=resolved.stdin,
        env_subset=dict(resolved.env_subset),
        stdout=resolved.stdout,
        stderr=resolved.stderr,
        exit_code=resolved.exit_code,
        timestamp=_TIMESTAMP,
        duration_ms=0,
    )


def make_invocation(
    command: str = "git",
    args: cabc.Sequence[str] | None = None,
    *,
    stdin: str = "",
    env: cabc.Mapping[str, str] | None = None,
) -> Invocation:
    """Build a live invocation with replay-test defaults.

    Parameters
    ----------
    command : str
        Command name; ``"git"`` by default.
    args : collections.abc.Sequence of str or None
        Arguments; ``None`` means ``["status"]``.
    stdin : str
        Captured standard input.
    env : collections.abc.Mapping of str to str or None
        Environment variables visible to the command.

    Returns
    -------
    Invocation
        The invocation under test.
    """
    return Invocation(
        command=command,
        args=["status"] if args is None else list(args),
        stdin=stdin,
        env={} if env is None else dict(env),
    )


def write_fixture_file(
    directory: Path,
    recordings: cabc.Sequence[RecordedInvocation],
    filename: str = "fixture.json",
) -> Path:
    """Persist *recordings* as a fixture file inside *directory*.

    Parameters
    ----------
    directory : pathlib.Path
        Directory to write into; it is created when absent.
    recordings : collections.abc.Sequence of RecordedInvocation
        Recordings to store, in fixture order.
    filename : str
        File name to use inside *directory*.

    Returns
    -------
    pathlib.Path
        The written fixture path.
    """
    fixture = FixtureFile(
        version=FixtureFile.SCHEMA_VERSION,
        metadata=FixtureMetadata.create(),
        recordings=list(recordings),
        scrubbing_rules=[],
    )
    path = directory / filename
    fixture.save(path)
    return path


def recorded_invocations() -> st.SearchStrategy[RecordedInvocation]:
    """Generate recorded invocations from arbitrary JSON-compatible values.

    Returns
    -------
    hypothesis.strategies.SearchStrategy
        A strategy covering the whole ``RecordedInvocation`` field space.
    """
    # Bounded text keeps generation cheap while still covering non-ASCII and
    # control characters, which is all the round-trip contract needs.
    return st.builds(
        RecordedInvocation,
        sequence=st.integers(),
        command=st.text(max_size=8),
        args=st.lists(st.text(max_size=8), max_size=4),
        stdin=st.text(max_size=8),
        env_subset=st.dictionaries(
            st.text(max_size=8), st.text(max_size=8), max_size=4
        ),
        stdout=st.text(max_size=8),
        stderr=st.text(max_size=8),
        exit_code=st.integers(),
        timestamp=st.text(max_size=8),
        duration_ms=st.integers(),
    )


def fixture_metadata() -> st.SearchStrategy[FixtureMetadata]:
    """Generate fixture metadata, including absent optional fields.

    Returns
    -------
    hypothesis.strategies.SearchStrategy
        A strategy over ``FixtureMetadata`` values.
    """
    return st.builds(
        FixtureMetadata,
        created_at=st.text(max_size=8),
        cmdmox_version=st.text(max_size=8),
        platform=st.text(max_size=8),
        python_version=st.text(max_size=8),
        test_module=st.none() | st.text(max_size=8),
        test_function=st.none() | st.text(max_size=8),
    )


def scrubbing_rules() -> st.SearchStrategy[ScrubbingRule]:
    """Generate scrubbing rules, including empty descriptions.

    Returns
    -------
    hypothesis.strategies.SearchStrategy
        A strategy over ``ScrubbingRule`` values.
    """
    return st.builds(
        ScrubbingRule,
        pattern=st.text(max_size=8),
        replacement=st.text(max_size=8),
        applied_to=st.lists(st.text(max_size=8), max_size=3),
        description=st.text(max_size=8),
    )


def fixture_files() -> st.SearchStrategy[FixtureFile]:
    """Generate whole fixture files that the writer can emit.

    Returns
    -------
    hypothesis.strategies.SearchStrategy
        A strategy over ``FixtureFile`` values at the current schema version.
    """
    return st.builds(
        FixtureFile,
        version=st.just(FixtureFile.SCHEMA_VERSION),
        metadata=fixture_metadata(),
        recordings=st.lists(recorded_invocations(), max_size=3),
        scrubbing_rules=st.lists(scrubbing_rules(), max_size=2),
    )


def _scenario_invocations() -> st.SearchStrategy[Invocation]:
    """Generate live invocations from the scenario alphabets."""
    return st.builds(
        make_invocation,
        command=st.sampled_from(_SCENARIO_COMMANDS),
        args=st.lists(st.sampled_from(_SCENARIO_ARGS), max_size=1),
        stdin=st.sampled_from(_SCENARIO_STDIN),
        env=st.dictionaries(
            st.sampled_from(_SCENARIO_ENV_KEYS),
            st.sampled_from(_SCENARIO_ENV_VALUES),
            max_size=2,
        ),
    )


def _scenario_recordings() -> st.SearchStrategy[RecordedInvocation]:
    """Generate unrelated recordings from the scenario alphabets."""
    return st.builds(
        RecordedInvocation,
        sequence=st.integers(min_value=0, max_value=2),
        command=st.sampled_from(_SCENARIO_COMMANDS),
        args=st.lists(st.sampled_from(_SCENARIO_ARGS), max_size=1),
        stdin=st.sampled_from(_SCENARIO_STDIN),
        env_subset=st.dictionaries(
            st.sampled_from(_SCENARIO_ENV_KEYS),
            st.sampled_from(_SCENARIO_ENV_VALUES),
            max_size=2,
        ),
        stdout=st.sampled_from(("ok\n", "other\n")),
        stderr=st.just(""),
        exit_code=st.sampled_from((0, 1)),
        timestamp=st.just(_TIMESTAMP),
        duration_ms=st.just(0),
    )


def _candidate_recording(
    invocation: Invocation,
) -> st.SearchStrategy[RecordedInvocation]:
    """Generate a recording compatible with *invocation*."""
    # Command and args are fixed to the invocation's so most recordings become
    # candidates; stdin, env_subset, and sequence still vary, which keeps stats
    # ties and every scoring class reachable.
    return st.builds(
        RecordedInvocation,
        sequence=st.integers(min_value=0, max_value=2),
        command=st.just(invocation.command),
        args=st.just(list(invocation.args)),
        stdin=st.sampled_from((invocation.stdin, "", "s", "other")),
        env_subset=st.dictionaries(
            st.sampled_from(_SCENARIO_ENV_KEYS),
            st.sampled_from(_SCENARIO_ENV_VALUES),
            max_size=2,
        ),
        stdout=st.sampled_from(("ok\n", "other\n")),
        stderr=st.just(""),
        exit_code=st.sampled_from((0, 1)),
        timestamp=st.just(_TIMESTAMP),
        duration_ms=st.just(0),
    )


@st.composite
def replay_scenarios(draw: st.DrawFn) -> ReplayScenario:
    """Generate matching scenarios with common command and env collisions.

    Two thirds of the recordings are drawn compatible with the invocation, so
    equal-stats ties and every scoring class occur often enough for
    :func:`hypothesis.find` to reach them.

    Parameters
    ----------
    draw : hypothesis.strategies.DrawFn
        Hypothesis draw function supplied by the ``composite`` decorator.

    Returns
    -------
    hypothesis.strategies.SearchStrategy
        A strategy over :class:`ReplayScenario` values.
    """
    strict = draw(st.booleans())
    invocation = draw(_scenario_invocations())
    # Two of the three arms are invocation-compatible, so most recordings are
    # candidates and ties are common; the list is built once rather than by
    # repeated draws so generation stays cheap.
    recording = st.one_of(
        _candidate_recording(invocation),
        _candidate_recording(invocation),
        _scenario_recordings(),
    )
    count = draw(st.integers(min_value=2, max_value=5))
    recordings = tuple(draw(recording) for _ in range(count))
    if recordings:
        consumed = frozenset(
            draw(st.sets(st.integers(min_value=0, max_value=len(recordings) - 1)))
        )
    else:
        consumed = frozenset()
    return ReplayScenario(
        strict=strict,
        invocation=invocation,
        recordings=recordings,
        consumed=consumed,
    )


def _oracle_matches(scenario: ReplayScenario, recording: RecordedInvocation) -> bool:
    """Reproduce strict and fuzzy compatibility without the matcher."""
    invocation = scenario.invocation
    if invocation.command != recording.command or invocation.args != recording.args:
        return False
    if not scenario.strict:
        return True
    if invocation.stdin != recording.stdin:
        return False
    return all(
        invocation.env.get(key) == value for key, value in recording.env_subset.items()
    )


def _stats(invocation: Invocation, recording: RecordedInvocation) -> _Stats:
    """Score one compatible recording exactly as the design specifies."""
    matching = sum(
        1
        for key, value in recording.env_subset.items()
        if invocation.env.get(key) == value
    )
    return (invocation.stdin == recording.stdin, matching, len(recording.env_subset))


def _rank_key(scenario: ReplayScenario, index: int) -> _RankKey:
    """Rank a candidate: higher stats, then lower sequence, then lower index."""
    recording = scenario.recordings[index]
    return (
        _stats(scenario.invocation, recording),
        -recording.sequence,
        -index,
    )


def _best_ranked(
    scenario: ReplayScenario,
    indices: cabc.Iterable[int],
) -> tuple[int, _RankKey] | None:
    """Return the highest-ranked index among *indices*, or ``None``."""
    best: tuple[int, _RankKey] | None = None
    for index in indices:
        key = _rank_key(scenario, index)
        if best is None or key > best[1]:
            best = (index, key)
    return best


def _candidate_indices(scenario: ReplayScenario) -> list[int]:
    """Return the unconsumed, compatible indices in fixture order."""
    return [
        index
        for index, recording in enumerate(scenario.recordings)
        if index not in scenario.consumed and _oracle_matches(scenario, recording)
    ]


def oracle_find_match(scenario: ReplayScenario) -> int | None:
    """Independently compute the index ``InvocationMatcher`` should select.

    Parameters
    ----------
    scenario : ReplayScenario
        The invocation, recordings, consumed set, and matching mode.

    Returns
    -------
    int or None
        The best-fit unconsumed index, or ``None`` when none is compatible.
    """
    best = _best_ranked(scenario, _candidate_indices(scenario))
    return None if best is None else best[0]


def classify_scenario(scenario: ReplayScenario) -> str:
    """Label how the winner beat the best of the other candidates.

    Parameters
    ----------
    scenario : ReplayScenario
        The scenario to classify.

    Returns
    -------
    str
        One of ``"no candidate"``, ``"single candidate"``,
        ``"won on stdin"``, ``"won on env pairs"``, ``"won on subset size"``,
        ``"won on sequence"``, or ``"won on list order"``.
    """
    candidates = _candidate_indices(scenario)
    if not candidates:
        return "no candidate"
    best = _best_ranked(scenario, candidates)
    if best is None:
        return "no candidate"
    winner, win_key = best
    runner = _best_ranked(scenario, [index for index in candidates if index != winner])
    if runner is None:
        return "single candidate"
    _, runner_key = runner
    for position, label in enumerate(_SCORE_LABELS):
        if win_key[0][position] > runner_key[0][position]:
            return label
    if win_key[1] > runner_key[1]:
        return "won on sequence"
    return "won on list order"


def check_find_match(
    scenario: ReplayScenario,
    find_match: cabc.Callable[
        [Invocation, list[RecordedInvocation], set[int]], int | None
    ]
    | None = None,
) -> None:
    """Assert one selection agrees with the oracle and mutates no input.

    Parameters
    ----------
    scenario : ReplayScenario
        The scenario to exercise.
    find_match : collections.abc.Callable or None
        Selection function to test; ``None`` uses ``InvocationMatcher``.
        A seeded mutant is passed here to prove this check can fail.

    Raises
    ------
    AssertionError
        If the selection disagrees with the oracle or either input mutated.
    """
    recordings = list(scenario.recordings)
    consumed = set(scenario.consumed)
    expected = oracle_find_match(scenario)
    if find_match is None:
        matcher = InvocationMatcher(strict=scenario.strict)
        actual = matcher.find_match(scenario.invocation, recordings, consumed)
    else:
        actual = find_match(scenario.invocation, recordings, consumed)
    if actual != expected:
        msg = (
            f"find_match selected {actual}, oracle expected {expected} "
            f"for scenario {scenario!r}"
        )
        raise AssertionError(msg)
    if recordings != list(scenario.recordings) or consumed != set(scenario.consumed):
        msg = f"find_match mutated its inputs for scenario {scenario!r}"
        raise AssertionError(msg)
