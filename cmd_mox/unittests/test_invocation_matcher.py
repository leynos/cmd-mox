"""Unit tests for InvocationMatcher boolean matching and best-fit selection."""

from __future__ import annotations

import dataclasses as dc
import typing as t

import pytest

from cmd_mox.ipc import Invocation
from cmd_mox.record.fixture import RecordedInvocation
from cmd_mox.record.matching import InvocationMatcher


@dc.dataclass
class RecordedInvocationSpec:
    """Optional overrides for building a RecordedInvocation."""

    stdin: str = ""
    env_subset: dict[str, str] = dc.field(default_factory=dict)
    stdout: str = "ok\n"
    stderr: str = ""
    exit_code: int = 0
    sequence: int = 0


def _make_recorded_invocation(
    command: str = "git",
    args: list[str] | None = None,
    spec: RecordedInvocationSpec | None = None,
) -> RecordedInvocation:
    """Build a RecordedInvocation with sensible defaults."""
    s = spec or RecordedInvocationSpec()
    return RecordedInvocation(
        sequence=s.sequence,
        command=command,
        args=["status"] if args is None else args,
        stdin=s.stdin,
        env_subset=s.env_subset,
        stdout=s.stdout,
        stderr=s.stderr,
        exit_code=s.exit_code,
        timestamp="2026-01-15T10:30:00+00:00",
        duration_ms=0,
    )


def _make_invocation(
    command: str = "git",
    args: list[str] | None = None,
    stdin: str = "",
    env: dict[str, str] | None = None,
) -> Invocation:
    """Build an Invocation with sensible defaults."""
    return Invocation(
        command=command,
        args=["status"] if args is None else args,
        stdin=stdin,
        env=env or {},
    )


class TestInvocationMatcherStrictMode:
    """Tests for InvocationMatcher.matches() in strict mode."""

    def test_exact_match_returns_true(self) -> None:
        """Exact match with command, args, stdin, env_subset returns True."""
        matcher = InvocationMatcher(strict=True)
        inv = _make_invocation(stdin="data", env={"FOO": "bar"})
        rec = _make_recorded_invocation(
            spec=RecordedInvocationSpec(stdin="data", env_subset={"FOO": "bar"})
        )

        assert matcher.matches(inv, rec) is True

    def test_command_mismatch_returns_false(self) -> None:
        """Different command returns False."""
        matcher = InvocationMatcher(strict=True)
        inv = _make_invocation(command="curl")
        rec = _make_recorded_invocation(command="git")

        assert matcher.matches(inv, rec) is False

    def test_args_mismatch_returns_false(self) -> None:
        """Same command but different args returns False."""
        matcher = InvocationMatcher(strict=True)
        inv = _make_invocation(args=["pull"])
        rec = _make_recorded_invocation(args=["status"])

        assert matcher.matches(inv, rec) is False

    def test_stdin_mismatch_returns_false(self) -> None:
        """Matching command+args but different stdin returns False in strict mode."""
        matcher = InvocationMatcher(strict=True)
        inv = _make_invocation(stdin="expected")
        rec = _make_recorded_invocation(spec=RecordedInvocationSpec(stdin="different"))

        assert matcher.matches(inv, rec) is False

    def test_env_subset_mismatch_returns_false(self) -> None:
        """Env_subset key present but with wrong value returns False."""
        matcher = InvocationMatcher(strict=True)
        inv = _make_invocation(env={"GIT_DIR": "/other"})
        rec = _make_recorded_invocation(
            spec=RecordedInvocationSpec(env_subset={"GIT_DIR": ".git"})
        )

        assert matcher.matches(inv, rec) is False

    def test_env_subset_containment_semantics(self) -> None:
        """Extra env keys in invocation do not prevent matching."""
        matcher = InvocationMatcher(strict=True)
        inv = _make_invocation(env={"GIT_DIR": ".git", "EXTRA": "value"})
        rec = _make_recorded_invocation(
            spec=RecordedInvocationSpec(env_subset={"GIT_DIR": ".git"})
        )

        assert matcher.matches(inv, rec) is True


class TestInvocationMatcherFuzzyMode:
    """Tests for InvocationMatcher.matches() in fuzzy mode."""

    def test_fuzzy_mode_ignores_stdin(self) -> None:
        """In fuzzy mode, stdin differences do not prevent matching."""
        matcher = InvocationMatcher(strict=False)
        inv = _make_invocation(stdin="different input")
        rec = _make_recorded_invocation(
            spec=RecordedInvocationSpec(stdin="recorded input")
        )

        assert matcher.matches(inv, rec) is True

    def test_fuzzy_mode_ignores_env(self) -> None:
        """In fuzzy mode, env differences do not prevent matching."""
        matcher = InvocationMatcher(strict=False)
        inv = _make_invocation(env={"FOO": "bar"})
        rec = _make_recorded_invocation(
            spec=RecordedInvocationSpec(env_subset={"FOO": "different"})
        )

        assert matcher.matches(inv, rec) is True

    def test_fuzzy_mode_still_requires_command_match(self) -> None:
        """Fuzzy mode still requires command equality."""
        matcher = InvocationMatcher(strict=False)
        inv = _make_invocation(command="curl")
        rec = _make_recorded_invocation(command="git")

        assert matcher.matches(inv, rec) is False

    def test_fuzzy_mode_still_requires_args_match(self) -> None:
        """Fuzzy mode still requires args equality."""
        matcher = InvocationMatcher(strict=False)
        inv = _make_invocation(args=["pull"])
        rec = _make_recorded_invocation(args=["status"])

        assert matcher.matches(inv, rec) is False


class TestInvocationMatcherFindMatch:
    """Tests for InvocationMatcher.find_match() selection logic."""

    def test_find_match_returns_none_when_no_candidates(self) -> None:
        """find_match returns None when no recordings match."""
        matcher = InvocationMatcher(strict=True)
        inv = _make_invocation(command="curl")
        recordings = [_make_recorded_invocation(command="git")]
        consumed = set[int]()

        result = matcher.find_match(inv, recordings, consumed)
        assert result is None

    def test_find_match_skips_consumed_indices(self) -> None:
        """find_match skips indices in the consumed set."""
        matcher = InvocationMatcher(strict=True)
        inv = _make_invocation()
        recordings = [
            _make_recorded_invocation(spec=RecordedInvocationSpec(sequence=0)),
            _make_recorded_invocation(spec=RecordedInvocationSpec(sequence=1)),
        ]
        consumed = {0}

        result = matcher.find_match(inv, recordings, consumed)
        assert result == 1

    def test_find_match_returns_single_compatible_recording(self) -> None:
        """find_match returns index 0 when only one recording is compatible."""
        matcher = InvocationMatcher(strict=True)
        inv = _make_invocation()
        recordings = [_make_recorded_invocation()]
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
        inv = _make_invocation(env=inv_env)
        recordings = [
            # Generic: empty env_subset, always matches in strict mode
            _make_recorded_invocation(
                spec=RecordedInvocationSpec(sequence=0, env_subset={}, stdout="generic")
            ),
            # Specific: env_subset may or may not match
            _make_recorded_invocation(
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
        matcher_kwargs: dict[str, t.Any],
        inv_kwargs: dict[str, t.Any],
        rec_specs: list[RecordedInvocationSpec],
        expected: int | None,
    ) -> None:
        """Test best-fit selection across different matching scenarios."""
        matcher = InvocationMatcher(**matcher_kwargs)
        inv = _make_invocation(**inv_kwargs)
        recordings = [_make_recorded_invocation(spec=s) for s in rec_specs]
        consumed = set[int]()

        result = matcher.find_match(inv, recordings, consumed)
        assert result == expected

    def test_fuzzy_best_fit_prefers_more_env_matches(self) -> None:
        """In fuzzy mode, prefer candidate with more matching env pairs."""
        matcher = InvocationMatcher(strict=False)
        inv = _make_invocation(env={"FOO": "bar", "BAZ": "qux"})
        recordings = [
            # First has 1 matching env pair
            _make_recorded_invocation(
                spec=RecordedInvocationSpec(
                    sequence=0, env_subset={"FOO": "bar"}, stdout="one"
                )
            ),
            # Second has 2 matching env pairs
            _make_recorded_invocation(
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
