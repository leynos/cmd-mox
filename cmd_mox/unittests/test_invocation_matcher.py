"""Unit tests for InvocationMatcher boolean matching and best-fit selection."""

from __future__ import annotations

import dataclasses as dc
import typing as typ

import pytest

from cmd_mox.record.matching import InvocationMatcher
from cmd_mox.unittests._replay_helpers import (
    RecordedInvocationSpec,
    make_invocation,
    make_recorded_invocation,
)

if typ.TYPE_CHECKING:
    from cmd_mox.unittests._invocation_helpers import InvocationKwargs


@dc.dataclass(slots=True, frozen=True)
class MatchCase:
    """One InvocationMatcher.matches() test case."""

    strict: bool
    spec: RecordedInvocationSpec
    inv_kwargs: InvocationKwargs
    expected: bool

    @property
    def mode(self) -> str:
        """The matcher mode implied by *strict*.

        Returns
        -------
        str
            ``"strict"`` when strict matching is enabled, otherwise ``"fuzzy"``.
        """
        return "strict" if self.strict else "fuzzy"


def _assert_match(case: MatchCase) -> None:
    """Assert that one invocation matches its recording as expected."""
    matcher = InvocationMatcher(strict=case.strict)
    invocation = make_invocation(**case.inv_kwargs)
    recording = make_recorded_invocation(case.spec)

    assert matcher.matches(invocation, recording) is case.expected, (
        f"{case.mode} matches() should be {case.expected} "
        f"for invocation {case.inv_kwargs}"
    )


class TestInvocationMatcherStrictMode:
    """Tests for InvocationMatcher.matches() in strict mode."""

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                MatchCase(
                    strict=True,
                    spec=RecordedInvocationSpec(
                        stdin="data", env_subset={"FOO": "bar"}
                    ),
                    inv_kwargs={"stdin": "data", "env": {"FOO": "bar"}},
                    expected=True,
                ),
                id="exact-match",
            ),
            pytest.param(
                MatchCase(
                    strict=True,
                    spec=RecordedInvocationSpec(),
                    inv_kwargs={"command": "curl"},
                    expected=False,
                ),
                id="command-differs",
            ),
            pytest.param(
                MatchCase(
                    strict=True,
                    spec=RecordedInvocationSpec(),
                    inv_kwargs={"args": ["pull"]},
                    expected=False,
                ),
                id="args-differ",
            ),
            pytest.param(
                MatchCase(
                    strict=True,
                    spec=RecordedInvocationSpec(stdin="different"),
                    inv_kwargs={"stdin": "expected"},
                    expected=False,
                ),
                id="stdin-differs",
            ),
            pytest.param(
                MatchCase(
                    strict=True,
                    spec=RecordedInvocationSpec(env_subset={"GIT_DIR": ".git"}),
                    inv_kwargs={"env": {"GIT_DIR": "/other"}},
                    expected=False,
                ),
                id="env-subset-not-contained",
            ),
            pytest.param(
                MatchCase(
                    strict=True,
                    spec=RecordedInvocationSpec(env_subset={"GO_EXTLINK_ENABLED": "1"}),
                    inv_kwargs={"env": {"GO_EXTLINK_ENABLED": "0"}},
                    expected=False,
                ),
                id="go-extlink-enabled-differs",
            ),
            pytest.param(
                MatchCase(
                    strict=True,
                    spec=RecordedInvocationSpec(
                        env_subset={"GORACE": "halt_on_error=1"}
                    ),
                    inv_kwargs={"env": {"GORACE": "halt_on_error=0"}},
                    expected=False,
                ),
                id="gorace-differs",
            ),
            pytest.param(
                MatchCase(
                    strict=True,
                    spec=RecordedInvocationSpec(env_subset={"GIT_DIR": ".git"}),
                    inv_kwargs={"env": {"GIT_DIR": ".git", "EXTRA": "value"}},
                    expected=True,
                ),
                id="env-subset-contained",
            ),
        ],
    )
    def test_strict_mode_requires_command_args_stdin_and_env_subset(
        self,
        case: MatchCase,
    ) -> None:
        """Strict mode matches only when command, args, stdin, and env agree."""
        _assert_match(case)


class TestInvocationMatcherFuzzyMode:
    """Tests for InvocationMatcher.matches() in fuzzy mode."""

    @pytest.mark.parametrize(
        "case",
        [
            pytest.param(
                MatchCase(
                    strict=False,
                    spec=RecordedInvocationSpec(stdin="recorded input"),
                    inv_kwargs={"stdin": "different input"},
                    expected=True,
                ),
                id="ignores-stdin",
            ),
            pytest.param(
                MatchCase(
                    strict=False,
                    spec=RecordedInvocationSpec(env_subset={"FOO": "different"}),
                    inv_kwargs={"env": {"FOO": "bar"}},
                    expected=True,
                ),
                id="ignores-env",
            ),
            pytest.param(
                MatchCase(
                    strict=False,
                    spec=RecordedInvocationSpec(),
                    inv_kwargs={"command": "curl"},
                    expected=False,
                ),
                id="requires-command",
            ),
            pytest.param(
                MatchCase(
                    strict=False,
                    spec=RecordedInvocationSpec(),
                    inv_kwargs={"args": ["pull"]},
                    expected=False,
                ),
                id="requires-args",
            ),
        ],
    )
    def test_fuzzy_mode_ignores_stdin_and_env_but_requires_command_and_args(
        self,
        case: MatchCase,
    ) -> None:
        """Fuzzy mode ignores stdin and env yet still requires command and args."""
        _assert_match(case)


class TestInvocationMatcherFindMatch:
    """Tests for InvocationMatcher.find_match() selection logic."""

    def test_find_match_returns_none_when_no_candidates(self) -> None:
        """find_match returns None when no recordings match."""
        matcher = InvocationMatcher(strict=True)
        inv = make_invocation(command="curl")
        recordings = [make_recorded_invocation(RecordedInvocationSpec(command="git"))]
        consumed = set[int]()

        result = matcher.find_match(inv, recordings, consumed)
        assert result is None

    def test_find_match_skips_consumed_indices(self) -> None:
        """find_match skips indices in the consumed set."""
        matcher = InvocationMatcher(strict=True)
        inv = make_invocation()
        recordings = [
            make_recorded_invocation(RecordedInvocationSpec(sequence=0)),
            make_recorded_invocation(RecordedInvocationSpec(sequence=1)),
        ]
        consumed = {0}

        result = matcher.find_match(inv, recordings, consumed)
        assert result == 1

    def test_find_match_returns_single_compatible_recording(self) -> None:
        """find_match returns index 0 when only one recording is compatible."""
        matcher = InvocationMatcher(strict=True)
        inv = make_invocation()
        recordings = [make_recorded_invocation()]
        consumed = set[int]()

        result = matcher.find_match(inv, recordings, consumed)
        assert result == 0

    @pytest.mark.parametrize(
        ("inv_env", "specific_env_subset", "expected"),
        [
            pytest.param(
                {"FOO": "bar", "BAZ": "qux"},
                {"FOO": "bar"},
                1,
                id="prefers_matching_env_subset",
            ),
            pytest.param(
                {"FOO": "bar"},
                {"FOO": "nope"},
                0,
                id="falls_back_to_generic_on_mismatch",
            ),
        ],
    )
    def test_strict_env_subset_selection(
        self,
        inv_env: dict[str, str],
        specific_env_subset: dict[str, str],
        expected: int,
    ) -> None:
        """Strict mode prefers specific env_subset; falls back on mismatch."""
        matcher = InvocationMatcher(strict=True)
        inv = make_invocation(env=inv_env)
        recordings = [
            # Generic: empty env_subset, always matches in strict mode
            make_recorded_invocation(
                spec=RecordedInvocationSpec(sequence=0, env_subset={}, stdout="generic")
            ),
            # Specific: env_subset may or may not match
            make_recorded_invocation(
                spec=RecordedInvocationSpec(
                    sequence=1, env_subset=specific_env_subset, stdout="specific"
                )
            ),
        ]
        consumed = set[int]()

        result = matcher.find_match(inv, recordings, consumed)
        assert result == expected

    @pytest.mark.parametrize(
        ("matcher_kwargs", "inv_kwargs", "rec_specs", "expected"),
        [
            pytest.param(
                {"strict": True},
                {"stdin": "hello"},
                [
                    RecordedInvocationSpec(sequence=0, stdin="hello", stdout="exact"),
                    RecordedInvocationSpec(sequence=1, stdin="", stdout="empty"),
                ],
                0,
                id="strict_prefers_exact_stdin",
            ),
            pytest.param(
                {"strict": False},
                {"stdin": "payload"},
                [
                    RecordedInvocationSpec(sequence=0, stdin="other", stdout="wrong"),
                    RecordedInvocationSpec(sequence=1, stdin="payload", stdout="right"),
                ],
                1,
                id="fuzzy_prefers_matching_stdin",
            ),
            pytest.param(
                {"strict": True},
                {},
                [
                    RecordedInvocationSpec(sequence=0, stdout="first"),
                    RecordedInvocationSpec(sequence=1, stdout="second"),
                ],
                0,
                id="tie_breaking_prefers_earlier_sequence",
            ),
            pytest.param(
                {"strict": False},
                {"stdin": "mismatch", "env": {"K": "wrong"}},
                [
                    RecordedInvocationSpec(
                        sequence=0,
                        stdin="recorded",
                        env_subset={"K": "v"},
                        stdout="first",
                    ),
                    RecordedInvocationSpec(
                        sequence=1,
                        stdin="recorded",
                        env_subset={"K": "v"},
                        stdout="second",
                    ),
                ],
                0,
                id="fuzzy_with_differing_stdin_and_env_still_matches",
            ),
        ],
    )
    def test_find_match_best_fit_selection(
        self,
        matcher_kwargs: dict[str, typ.Any],
        inv_kwargs: dict[str, typ.Any],
        rec_specs: list[RecordedInvocationSpec],
        expected: int | None,
    ) -> None:
        """Test best-fit selection across different matching scenarios."""
        matcher = InvocationMatcher(**matcher_kwargs)
        inv = make_invocation(**inv_kwargs)
        recordings = [make_recorded_invocation(s) for s in rec_specs]
        consumed = set[int]()

        result = matcher.find_match(inv, recordings, consumed)
        assert result == expected

    def test_fuzzy_best_fit_prefers_more_env_matches(self) -> None:
        """In fuzzy mode, prefer candidate with more matching env pairs."""
        matcher = InvocationMatcher(strict=False)
        inv = make_invocation(env={"FOO": "bar", "BAZ": "qux"})
        recordings = [
            # First has 1 matching env pair
            make_recorded_invocation(
                spec=RecordedInvocationSpec(
                    sequence=0, env_subset={"FOO": "bar"}, stdout="one"
                )
            ),
            # Second has 2 matching env pairs
            make_recorded_invocation(
                spec=RecordedInvocationSpec(
                    sequence=1,
                    env_subset={"FOO": "bar", "BAZ": "qux"},
                    stdout="two",
                )
            ),
        ]
        consumed = set[int]()

        result = matcher.find_match(inv, recordings, consumed)
        # Should prefer index 1 (more matching env pairs)
        assert result == 1


class TestInvocationMatcherDiscriminatingCases:
    """Cases that separate rules the earlier tables could not tell apart."""

    def test_missing_live_env_key_never_satisfies_a_pair(self) -> None:
        """A recorded env pair needs its key present in the live environment."""
        matcher = InvocationMatcher(strict=True)
        recording = make_recorded_invocation(
            RecordedInvocationSpec(env_subset={"MISSING": "value"})
        )

        assert matcher.matches(make_invocation(env={}), recording) is False

    def test_empty_env_subset_matches_any_environment(self) -> None:
        """An empty env_subset imposes no constraint in strict mode."""
        matcher = InvocationMatcher(strict=True)
        recording = make_recorded_invocation(RecordedInvocationSpec(env_subset={}))

        assert matcher.matches(make_invocation(env={"ANY": "thing"}), recording) is True

    def test_all_candidates_consumed_returns_none(self) -> None:
        """Consuming every compatible recording leaves no candidate."""
        matcher = InvocationMatcher(strict=True)
        recordings = [
            make_recorded_invocation(RecordedInvocationSpec(sequence=0)),
            make_recorded_invocation(RecordedInvocationSpec(sequence=1)),
        ]

        assert matcher.find_match(make_invocation(), recordings, {0, 1}) is None

    def test_empty_recordings_list_returns_none(self) -> None:
        """An empty fixture has nothing to select."""
        matcher = InvocationMatcher(strict=False)

        assert matcher.find_match(make_invocation(), [], set()) is None

    def test_lower_sequence_later_in_the_list_wins(self) -> None:
        """The tie-break reads ``sequence``, not list position."""
        matcher = InvocationMatcher(strict=True)
        recordings = [
            make_recorded_invocation(RecordedInvocationSpec(sequence=5)),
            make_recorded_invocation(RecordedInvocationSpec(sequence=1)),
        ]

        assert matcher.find_match(make_invocation(), recordings, set()) == 1

    def test_equal_sequence_falls_back_to_list_order(self) -> None:
        """Equal ``sequence`` values keep the earlier list position."""
        matcher = InvocationMatcher(strict=True)
        recordings = [
            make_recorded_invocation(RecordedInvocationSpec(sequence=3)),
            make_recorded_invocation(RecordedInvocationSpec(sequence=3)),
        ]

        assert matcher.find_match(make_invocation(), recordings, set()) == 0

    def test_subset_size_decides_in_fuzzy_mode(self) -> None:
        """A larger env_subset wins when both candidates match one pair."""
        matcher = InvocationMatcher(strict=False)
        invocation = make_invocation(stdin="same", env={"K": "v"})
        recordings = [
            make_recorded_invocation(
                RecordedInvocationSpec(
                    sequence=0, stdin="same", env_subset={"K": "v", "J": "miss"}
                )
            ),
            make_recorded_invocation(
                RecordedInvocationSpec(sequence=1, stdin="same", env_subset={"K": "v"})
            ),
        ]

        assert matcher.find_match(invocation, recordings, set()) == 0
