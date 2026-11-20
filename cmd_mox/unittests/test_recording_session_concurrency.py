"""Concurrency tests for finalize and scrub behaviour in RecordingSession."""

from __future__ import annotations

import dataclasses as dc
import threading
import typing as typ

import pytest

from cmd_mox.atomic_write import AtomicWriteError
from cmd_mox.errors import LifecycleError
from cmd_mox.record.fixture import FixtureFile, RecordedInvocation
from cmd_mox.record.session import RecordingSession
from cmd_mox.unittests.test_recording_session import (
    _make_invocation,
    _make_response,
)

if typ.TYPE_CHECKING:
    from pathlib import Path


def _join_all(threads: list[threading.Thread], *, timeout: float = 10.0) -> None:
    """Join every thread, failing the test if any is still running."""
    for thread in threads:
        thread.join(timeout)
    assert not any(thread.is_alive() for thread in threads), (
        "worker thread did not finish; the operation may be deadlocked"
    )


class TestRecordingSessionConcurrency:
    """Tests for finalize and scrub concurrency guarantees."""

    def test_concurrent_finalize_returns_one_object_and_saves_once(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Concurrent finalize calls share one fixture and one write."""
        path = tmp_path / "out.json"
        session = RecordingSession(path)
        session.start()
        session.record(_make_invocation(), _make_response())

        save_calls: list[Path] = []
        original_save = FixtureFile.save

        def _counting_save(fixture: FixtureFile, destination: Path) -> None:
            """Record the destination, then delegate to the real save."""
            save_calls.append(destination)
            original_save(fixture, destination)

        monkeypatch.setattr(FixtureFile, "save", _counting_save)

        worker_count = 8
        barrier = threading.Barrier(worker_count)
        results: list[FixtureFile] = []
        errors: list[Exception] = []

        def _finalize() -> None:
            """Wait for every worker, then finalize the shared session."""
            try:
                barrier.wait(timeout=5)
                results.append(session.finalize())
            except Exception as exc:  # ruff: ignore[blind-except] - surfaced by the assertions
                errors.append(exc)

        threads = [
            threading.Thread(target=_finalize, daemon=True) for _ in range(worker_count)
        ]
        for thread in threads:
            thread.start()
        _join_all(threads)

        assert errors == []
        assert len(save_calls) == 1
        assert len(results) == worker_count
        first = results[0]
        assert all(result is first for result in results)
        assert len(FixtureFile.load(path).recordings) == 1

    def test_finalize_retries_after_a_save_failure(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failed save leaves the session finalized but retryable."""
        path = tmp_path / "out.json"
        session = RecordingSession(path)
        session.start()
        session.record(_make_invocation(), _make_response())

        attempts: list[Path] = []
        original_save = FixtureFile.save

        def _flaky_save(fixture: FixtureFile, destination: Path) -> None:
            """Fail the first write and delegate on later attempts."""
            attempts.append(destination)
            if len(attempts) == 1:
                message = "disk full"
                raise AtomicWriteError(message, path=destination)
            original_save(fixture, destination)

        monkeypatch.setattr(FixtureFile, "save", _flaky_save)

        with pytest.raises(AtomicWriteError):
            session.finalize()

        assert path.exists() is False
        with pytest.raises(LifecycleError):
            session.record(_make_invocation(), _make_response())

        fixture = session.finalize()

        assert len(attempts) == 2
        assert len(fixture.recordings) == 1
        assert len(FixtureFile.load(path).recordings) == 1
        assert session.finalize() is fixture

    def test_scrubber_may_call_back_into_the_session(self, tmp_path: Path) -> None:
        """A scrubber that calls a locked session method does not deadlock."""
        session = RecordingSession(tmp_path / "out.json")

        class _ReentrantScrubber:
            """Scrubber double that restarts the session during scrubbing."""

            def __init__(self) -> None:
                """Leave the session reference unset until construction ends."""
                self.session: RecordingSession | None = None

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Call a lock-taking session method, then return the recording."""
                assert self.session is not None
                self.session.start()
                return recording

        scrubber = _ReentrantScrubber()
        scrubber.session = session
        session._scrubber = scrubber
        session.start()

        errors: list[Exception] = []

        def _record() -> None:
            """Record one invocation, capturing any failure for the assertion."""
            try:
                session.record(_make_invocation(), _make_response())
            except Exception as exc:  # ruff: ignore[blind-except] - surfaced by the assertions
                errors.append(exc)

        thread = threading.Thread(target=_record, daemon=True)
        thread.start()
        _join_all([thread])

        assert errors == []
        assert len(session._recordings) == 1

    def test_finalize_during_a_scrub_rejects_the_recording(
        self, tmp_path: Path
    ) -> None:
        """Finalizing inside scrub() rejects the in-flight recording."""
        path = tmp_path / "out.json"
        session_holder: list[RecordingSession] = []

        class _FinalizingScrubber:
            """Scrubber double that finalizes the session from inside scrub()."""

            fixture: FixtureFile | None = None

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Finalize the session, then return the recording."""
                self.fixture = session_holder[0].finalize()
                return recording

        scrubber = _FinalizingScrubber()
        session = RecordingSession(path, scrubber=scrubber)
        session_holder.append(session)
        session.start()

        errors: list[Exception] = []

        def _record() -> None:
            """Record one invocation, capturing the expected lifecycle error."""
            try:
                session.record(_make_invocation(), _make_response())
            except Exception as exc:  # ruff: ignore[blind-except] - surfaced by the assertions
                errors.append(exc)

        thread = threading.Thread(target=_record, daemon=True)
        thread.start()
        _join_all([thread])

        assert len(errors) == 1
        assert isinstance(errors[0], LifecycleError)
        assert session._recordings == []
        assert scrubber.fixture is not None
        assert scrubber.fixture.recordings == []

    def test_scrubber_exception_propagates_without_consuming_a_sequence(
        self, tmp_path: Path
    ) -> None:
        """A failing scrub leaves no gap in the append-order sequence."""

        class _FlakyScrubber:
            """Scrubber double that fails while its flag is set."""

            should_fail = True

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Raise on demand; otherwise return the recording unchanged."""
                if self.should_fail:
                    message = "scrub failed"
                    raise RuntimeError(message)
                return recording

        scrubber = _FlakyScrubber()
        session = RecordingSession(tmp_path / "out.json", scrubber=scrubber)
        session.start()

        with pytest.raises(RuntimeError, match="scrub failed"):
            session.record(_make_invocation(), _make_response())

        assert session._recordings == []

        scrubber.should_fail = False
        session.record(_make_invocation(), _make_response())

        assert [recording.sequence for recording in session._recordings] == [0]

    def test_scrubber_cannot_override_the_assigned_sequence(
        self, tmp_path: Path
    ) -> None:
        """The session assigns the append-order sequence after scrubbing."""

        class _SequenceScrubber:
            """Scrubber double that tries to pin its own sequence number."""

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Return a copy with a deliberately wrong sequence."""
                return dc.replace(recording, sequence=99)

        session = RecordingSession(tmp_path / "out.json", scrubber=_SequenceScrubber())
        session.start()
        session.record(_make_invocation(), _make_response())
        session.record(_make_invocation(), _make_response())

        assert [recording.sequence for recording in session._recordings] == [0, 1]

    def test_scrubbers_run_outside_the_session_lock(self, tmp_path: Path) -> None:
        """Two scrubbers can run concurrently without holding the state lock."""
        barrier = threading.Barrier(2)

        class _BarrierScrubber:
            """Scrubber double that waits for a second concurrent scrub."""

            def scrub(self, recording: RecordedInvocation) -> RecordedInvocation:
                """Wait for the peer scrub, then return the recording."""
                barrier.wait(timeout=5)
                return recording

        session = RecordingSession(tmp_path / "out.json", scrubber=_BarrierScrubber())
        session.start()
        errors: list[Exception] = []

        def _record() -> None:
            """Record one invocation, capturing any failure for the assertion."""
            try:
                session.record(_make_invocation(), _make_response())
            except Exception as exc:  # ruff: ignore[blind-except] - surfaced by the assertions
                errors.append(exc)

        threads = [threading.Thread(target=_record, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        _join_all(threads)

        assert errors == []
        assert len(session._recordings) == 2
        assert sorted(recording.sequence for recording in session._recordings) == [
            0,
            1,
        ]
