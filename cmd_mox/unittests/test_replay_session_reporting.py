"""ReplaySession consumption reporting, exhaustion, and concurrency.

The message snapshots pin what a user sees when recordings are left unused;
the concurrency case proves each recording is consumed exactly once under
contention. Both are reporting-level guarantees rather than matching rules, so
they live apart from `test_replay_session.py`.
"""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox.errors import VerificationError
from cmd_mox.record.replay import ReplaySession
from cmd_mox.unittests._replay_helpers import (
    RecordedInvocationSpec,
    make_invocation,
    make_recorded_invocation,
    write_fixture_file,
)

if typ.TYPE_CHECKING:
    from pathlib import Path

    from syrupy.assertion import SnapshotAssertion

    from cmd_mox.ipc import Response
    from cmd_mox.record.fixture import RecordedInvocation


class TestReplaySessionConcurrency:
    """Concurrent matches consume each recording exactly once."""

    @pytest.mark.parametrize(
        ("recording_count", "thread_count"),
        [(3, 3), (3, 8)],
        ids=["threads-equal-recordings", "more-threads-than-recordings"],
    )
    def test_concurrent_matches_consume_each_recording_exactly_once(
        self,
        tmp_path: Path,
        recording_count: int,
        thread_count: int,
    ) -> None:
        """Exactly ``recording_count`` calls match; the rest return ``None``."""
        import threading

        recs = [
            make_recorded_invocation(
                RecordedInvocationSpec(sequence=i, stdout=f"out-{i}\n")
            )
            for i in range(recording_count)
        ]
        path = write_fixture_file(tmp_path, recs)
        session = ReplaySession(path)
        session.load()

        barrier = threading.Barrier(thread_count)
        results: list[Response | None] = [None] * thread_count
        completed = [False] * thread_count

        def _match(idx: int) -> None:
            """Match once, after every thread has reached the barrier."""
            barrier.wait()
            results[idx] = session.match(make_invocation())
            completed[idx] = True

        threads = [
            threading.Thread(target=_match, args=(i,)) for i in range(thread_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert all(completed), "every thread must reach its match() call"
        assert all(not thread.is_alive() for thread in threads), "a thread did not join"
        matches = [result.stdout for result in results if result is not None]
        assert len(matches) == recording_count, f"expected {recording_count} matches"
        assert sorted(matches) == sorted(
            f"out-{i}\n" for i in range(recording_count)
        ), "matched stdout values must be a permutation of the recordings"
        assert session.consumed_count == recording_count


THREE_UNCONSUMED = [
    make_recorded_invocation(
        RecordedInvocationSpec(command="git", args=("status",), sequence=0)
    ),
    make_recorded_invocation(
        RecordedInvocationSpec(command="git", args=("commit", "-m", "msg"), sequence=1)
    ),
    make_recorded_invocation(
        RecordedInvocationSpec(command="curl", args=("example.com",), sequence=2)
    ),
]


class TestVerifyAllConsumedMessage:
    """Snapshots of the user-visible ``verify_all_consumed()`` message."""

    @pytest.mark.parametrize(
        "recordings",
        [
            pytest.param(
                [
                    make_recorded_invocation(
                        RecordedInvocationSpec(command="true", args=())
                    )
                ],
                id="zero-argument",
            ),
            pytest.param(
                [
                    make_recorded_invocation(
                        RecordedInvocationSpec(
                            command="git", args=("status", "--short")
                        )
                    )
                ],
                id="multi-argument",
            ),
            pytest.param(THREE_UNCONSUMED, id="three-unconsumed"),
        ],
    )
    def test_verify_message(
        self,
        tmp_path: Path,
        recordings: list[RecordedInvocation],
        snapshot: SnapshotAssertion,
    ) -> None:
        """The message lists each unconsumed recording on its own line."""
        path = write_fixture_file(tmp_path, recordings)
        session = ReplaySession(path)
        session.load()

        with pytest.raises(VerificationError) as excinfo:
            session.verify_all_consumed()

        assert str(excinfo.value) == snapshot


class TestReplaySessionFuzzyExhaustion:
    """Fuzzy mode exhausts its recordings and then stops matching."""

    def test_fuzzy_match_returns_none_once_all_recordings_are_consumed(
        self, tmp_path: Path
    ) -> None:
        """Every fuzzy candidate is consumed before the miss."""
        recs = [
            make_recorded_invocation(
                RecordedInvocationSpec(sequence=0, stdin="first", stdout="one\n")
            ),
            make_recorded_invocation(
                RecordedInvocationSpec(sequence=1, stdin="second", stdout="two\n")
            ),
        ]
        path = write_fixture_file(tmp_path, recs)
        session = ReplaySession(path, strict_matching=False)
        session.load()

        first = session.match(make_invocation(stdin="unrelated"))
        second = session.match(make_invocation(stdin="unrelated"))
        third = session.match(make_invocation(stdin="unrelated"))

        assert first is not None
        assert second is not None
        assert third is None
        assert {first.stdout, second.stdout} == {"one\n", "two\n"}
        assert session.consumed_count == 2
