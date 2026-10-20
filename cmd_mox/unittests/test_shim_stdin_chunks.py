"""Property tests for decoding stdin delivered in arbitrary byte chunks."""

from __future__ import annotations

import dataclasses
import os
import queue
import sys
import tempfile
import time
import typing as typ
from itertools import pairwise
from unittest import mock

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from cmd_mox import shim
from cmd_mox.unittests._shim_test_support import (
    _assert_exit_code,
    _InjectedOSError,
    _InjectedRuntimeError,
)


@settings(deadline=None)
@given(
    prefix=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=32),
    suffix=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=32),
    extra_cuts=st.sets(st.integers(min_value=0, max_value=64), max_size=32),
)
def test_stdin_chunk_boundaries_preserve_utf8_and_newlines(
    prefix: str,
    suffix: str,
    extra_cuts: set[int],
) -> None:
    """Buffered and raw reads preserve decoding across arbitrary byte splits."""
    text = f"{prefix}\N{EURO SIGN}\r\n{suffix}\r"
    payload = text.encode("utf-8")
    euro_start = len(prefix.encode("utf-8"))
    cuts = {euro_start + 1, euro_start + 2, euro_start + 4}
    cuts.update(1 + cut % (len(payload) - 1) for cut in extra_cuts)
    boundaries = [0, *sorted(cuts), len(payload)]
    chunks = [payload[start:end] for start, end in pairwise(boundaries)]
    expected = text.replace("\r\n", "\n").replace("\r", "\n")
    buffered = _read_stdin_chunks(chunks, buffered=True)
    raw = _read_stdin_chunks(chunks, buffered=False)

    assert buffered == expected, "Buffered chunks changed decoded stdin text"
    assert raw == expected, "Raw chunks changed decoded stdin text"


_MAX_POLL_TIMEOUT_TICKS = (shim._shim_stdin.MAX_POLL_TIMEOUT_MS * 1024 + 999) // 1_000


def _poll_events(event_kind: str) -> list[tuple[int, int]]:
    """Return the scripted readiness result for one generated poll.

    Returns
    -------
    list[tuple[int, int]]
        A readiness event, or an empty list for the empty-result case.
    """
    return [(123, 1)] if event_kind == "ready" else []


@dataclasses.dataclass(frozen=True, slots=True)
class _PollScenario:
    """Generated inputs for one monotonic polling progression."""

    duration_ticks: int
    start_ticks: int
    expiry_boundary: str
    event_kinds: list[str]
    later_progress: list[int]


@dataclasses.dataclass(frozen=True, slots=True)
class _PollProgression:
    """Clock samples and poller state derived from one scenario."""

    scenario: _PollScenario
    deadline_ticks: int
    clock_ticks: list[int]
    poller: _ScriptedPoller
    context: shim._shim_stdin._ReadContext


class _ScriptedPoller:
    """Return generated poll results while recording requested waits."""

    def __init__(self, results: list[list[tuple[int, int]]]) -> None:
        """Set the readiness sequence consumed by successive polls."""
        self.results = results
        self.timeouts: list[int] = []

    def poll(self, timeout: int) -> list[tuple[int, int]]:
        """Record the requested wait and return the next scripted events.

        Returns
        -------
        list[tuple[int, int]]
            The next scripted readiness result.
        """
        self.timeouts.append(timeout)
        return self.results[len(self.timeouts) - 1]


def _build_clock_progression(scenario: _PollScenario) -> tuple[int, list[int]]:
    """Build exact binary-fraction clock samples around the deadline.

    Returns
    -------
    tuple[int, list[int]]
        The deadline and non-decreasing clock samples in 1/1024-second ticks.
    """
    deadline_ticks = scenario.start_ticks + scenario.duration_ticks
    first_progress = {
        "before": max(scenario.duration_ticks - 1, 0),
        "at": scenario.duration_ticks,
        "after": scenario.duration_ticks + 1,
    }[scenario.expiry_boundary]
    progress_deltas = [
        first_progress,
        *scenario.later_progress[: 2 * len(scenario.event_kinds) - 2],
    ]
    clock_ticks = [scenario.start_ticks]
    for progress in progress_deltas:
        clock_ticks.append(clock_ticks[-1] + progress)
    return deadline_ticks, clock_ticks


def _assert_poll_progression_step(
    progression: _PollProgression, poll_index: int
) -> bool:
    """Check one poll's budget and whether expiry ends the progression.

    Returns
    -------
    bool
        Whether this poll reaches expiry and ends the generated progression.
    """
    before = progression.clock_ticks[2 * poll_index]
    if before >= progression.deadline_ticks:
        result = shim._shim_stdin._poll_stdin(progression.context)
        assert len(progression.poller.timeouts) == poll_index, (
            "Expired poll waited past its deadline"
        )
        assert result is None, "Expired clock progress returned a poll result"
        return True

    remaining_ticks = progression.deadline_ticks - before
    expected_timeout = min(
        shim._shim_stdin.MAX_POLL_TIMEOUT_MS,
        (remaining_ticks * 1_000 + 1_023) // 1_024,
    )
    result = shim._shim_stdin._poll_stdin(progression.context)
    assert progression.poller.timeouts[-1] == expected_timeout, (
        "Poll wait did not ceil and cap the remaining deadline budget"
    )

    after = progression.clock_ticks[2 * poll_index + 1]
    expected_result = (
        None
        if after >= progression.deadline_ticks
        else _poll_events(progression.scenario.event_kinds[poll_index])
    )
    assert result == expected_result, "Poll result crossed the deadline boundary"
    return expected_result is None


def _assert_poll_progression(scenario: _PollScenario) -> None:
    """Exercise generated poll outcomes against a controlled monotonic clock."""
    deadline_ticks, clock_ticks = _build_clock_progression(scenario)
    poller = _ScriptedPoller([_poll_events(kind) for kind in scenario.event_kinds])
    deadline = deadline_ticks / 1024
    progression = _PollProgression(
        scenario=scenario,
        deadline_ticks=deadline_ticks,
        clock_ticks=clock_ticks,
        poller=poller,
        context=shim._shim_stdin._ReadContext(
            descriptor=123,
            poller=poller,
            deadline=deadline,
            on_error=lambda exc: pytest.fail(f"Unexpected poll error: {exc}"),
        ),
    )
    with mock.patch.object(
        shim._shim_stdin.time,
        "monotonic",
        side_effect=[ticks / 1024 for ticks in progression.clock_ticks],
    ):
        for poll_index in range(len(scenario.event_kinds)):
            if _assert_poll_progression_step(progression, poll_index):
                break
    assert progression.clock_ticks == sorted(progression.clock_ticks), (
        "Controlled monotonic time went backwards"
    )


@given(
    scenario=st.builds(
        _PollScenario,
        duration_ticks=st.one_of(
            st.integers(min_value=1, max_value=10_000),
            st.sampled_from([
                1,
                127,
                128,
                1_001,
                _MAX_POLL_TIMEOUT_TICKS,
                _MAX_POLL_TIMEOUT_TICKS + 1,
                _MAX_POLL_TIMEOUT_TICKS + 2,
            ]),
        ),
        start_ticks=st.integers(min_value=0, max_value=4_096),
        expiry_boundary=st.sampled_from(["before", "at", "after"]),
        event_kinds=st.lists(
            st.sampled_from(["empty", "ready"]), min_size=1, max_size=4
        ),
        later_progress=st.lists(
            st.integers(min_value=0, max_value=10_000), min_size=6, max_size=6
        ),
    )
)
@example(
    scenario=_PollScenario(
        duration_ticks=129,
        start_ticks=0,
        expiry_boundary="before",
        event_kinds=["empty", "ready"],
        later_progress=[0, 0, 0, 0, 0, 0],
    )
)
@example(
    scenario=_PollScenario(
        duration_ticks=_MAX_POLL_TIMEOUT_TICKS + 1,
        start_ticks=0,
        expiry_boundary="before",
        event_kinds=["empty", "ready"],
        later_progress=[0, 0, 0, 0, 0, 0],
    )
)
@example(
    scenario=_PollScenario(
        duration_ticks=129,
        start_ticks=0,
        expiry_boundary="at",
        event_kinds=["ready"],
        later_progress=[0, 0, 0, 0, 0, 0],
    )
)
@example(
    scenario=_PollScenario(
        duration_ticks=129,
        start_ticks=0,
        expiry_boundary="at",
        event_kinds=["empty"],
        later_progress=[0, 0, 0, 0, 0, 0],
    )
)
@example(
    scenario=_PollScenario(
        duration_ticks=129,
        start_ticks=0,
        expiry_boundary="after",
        event_kinds=["ready"],
        later_progress=[0, 0, 0, 0, 0, 0],
    )
)
def test_poll_result_respects_deadline_for_ready_and_empty_events(
    scenario: _PollScenario,
) -> None:
    """Poll progress, outcomes, and waits stay within the monotonic budget."""
    _assert_poll_progression(scenario)


@pytest.mark.skipif(os.name == "nt", reason="stdin polling is POSIX-specific")
def test_raw_stdin_readiness_at_deadline_reports_timeout_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Readiness at expiry cannot turn a raw EOF into a successful read."""
    clock = {"value": 0.0}
    raw_reads: list[tuple[int, int]] = []

    class _ReadyAtExpiryPoll:
        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            """Advance fake time to expiry while reporting descriptor readiness.

            Returns
            -------
            list[tuple[int, int]]
                One readiness event after the deadline has expired.
            """
            clock["value"] = 1.0
            return [(123, 1)]

    class _UnbufferedStdin:
        encoding = "utf-8"
        errors = "strict"
        buffer = None

    def record_error(exc: Exception) -> typ.NoReturn:
        """Collect the timeout before exiting through the test callback.

        Raises
        ------
        _ErrorReportedError
            Stop after recording the error routed by the stdin reader.
        """
        reported.append(exc)
        raise _ErrorReportedError

    class _ErrorReportedError(Exception):
        pass

    reported: list[Exception] = []

    def read_eof(descriptor: int, size: int) -> bytes:
        """Record any raw read after expiry and return simulated EOF.

        Returns
        -------
        bytes
            An empty EOF marker.
        """
        raw_reads.append((descriptor, size))
        return b""

    context = shim._shim_stdin._ReadContext(
        descriptor=123,
        poller=_ReadyAtExpiryPoll(),
        deadline=1.0,
        on_error=record_error,
    )
    monkeypatch.setattr(sys, "stdin", _UnbufferedStdin())
    monkeypatch.setattr(shim._shim_stdin.time, "monotonic", lambda: clock["value"])
    monkeypatch.setattr(shim._shim_stdin.os, "read", read_eof)

    with pytest.raises(_ErrorReportedError):
        shim._shim_stdin._read_raw_polled_stdin(context)

    assert len(reported) == 1, "Expired readiness must report one timeout"
    assert isinstance(reported[0], TimeoutError), (
        "Deadline expiry was not reported as timeout"
    )
    assert raw_reads == [], "Raw EOF was consumed after the deadline"


def _read_stdin_chunks(chunks: list[bytes], *, buffered: bool) -> str:
    """Decode byte chunks through one of the shim's polled stdin paths.

    Returns
    -------
    str
        Decoded text with universal newlines translated.
    """

    class _ReadyPoller:
        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            """Report the synthetic stdin descriptor as ready.

            Returns
            -------
            list[tuple[int, int]]
                One readiness event for the synthetic descriptor.
            """
            return [(123, 1)]

    class _BufferedReader:
        def __init__(self) -> None:
            """Queue each supplied chunk followed by an EOF marker."""
            self.byte_chunks = iter([*chunks, b""])

        def read1(self, _size: int) -> bytes:
            """Return the next chunk for the buffered stdin path.

            Returns
            -------
            bytes
                The next scripted byte chunk.
            """
            return next(self.byte_chunks)

    class _Stdin:
        encoding = "utf-8"
        errors = "strict"

        def __init__(self) -> None:
            """Attach a fresh buffered reader to the synthetic stdin stream."""
            self.buffer = _BufferedReader()

    def fail_read(exc: Exception) -> typ.NoReturn:
        """Propagate read failures so the property test fails immediately."""
        raise exc

    stdin = _Stdin()
    context = shim._shim_stdin._ReadContext(
        descriptor=123,
        poller=_ReadyPoller(),
        deadline=time.monotonic() + 10.0,
        on_error=fail_read,
    )
    with mock.patch.object(sys, "stdin", stdin):
        if buffered:
            return shim._shim_stdin._read_buffered_stdin(context, stdin.buffer.read1)

        raw_chunks = iter([*chunks, b""])
        with mock.patch.object(
            shim._shim_stdin.os,
            "read",
            side_effect=lambda _descriptor, _size: next(raw_chunks),
        ):
            return shim._shim_stdin._read_raw_polled_stdin(context)


def test_decode_stdin_chunk_reports_invalid_text() -> None:
    """Decode failures flow through the shim's controlled error callback."""

    def raise_error(exc: Exception) -> typ.NoReturn:
        """Propagate the decode error for the controlled-error assertion."""
        raise exc

    with pytest.raises(UnicodeDecodeError):
        shim._shim_stdin._decode_stdin_chunk(
            shim._shim_stdin._stdin_decoder(), b"\xff", raise_error
        )


@pytest.mark.skipif(os.name == "nt", reason="regular-file worker is POSIX-specific")
def test_regular_stdin_reads_descriptor_without_buffered_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regular-file worker uses raw reads and preserves text decoding."""
    chunks = iter([b"\xe2\x82", b"\xac\r\n", b""])
    descriptors: list[int] = []

    class _RegularStdin:
        encoding = "utf-8"
        errors = "strict"

        def __init__(self, descriptor: int) -> None:
            """Track whether the text wrapper is bypassed by raw reads."""
            self.descriptor = descriptor
            self.read_calls = 0

        def fileno(self) -> int:
            """Return the regular-file descriptor used for raw input.

            Returns
            -------
            int
                The regular-file descriptor under test.
            """
            return self.descriptor

        def read(self) -> str:
            """Count buffered reads that should not occur on this path.

            Returns
            -------
            str
                A sentinel value that identifies the buffered path.
            """
            self.read_calls += 1
            return "buffered text"

    with tempfile.TemporaryFile() as regular_file:
        stdin = _RegularStdin(regular_file.fileno())
        monkeypatch.setattr(sys, "stdin", stdin)

        def read_raw(descriptor: int, _size: int) -> bytes:
            """Capture the file descriptor and provide the next raw chunk.

            Returns
            -------
            bytes
                The next scripted raw input chunk.
            """
            descriptors.append(descriptor)
            return next(chunks)

        monkeypatch.setattr(shim._shim_stdin.os, "read", read_raw)
        result = shim._shim_stdin._read_stdin_until_eof(
            1.0,
            deadline=None,
            on_error=shim._exit_ipc_error,
        )

    assert result == "\N{EURO SIGN}\n", (
        "Raw stdin chunks were not incrementally decoded and newline-translated"
    )
    assert descriptors, "Regular stdin reader did not call os.read"
    assert all(descriptor == stdin.descriptor for descriptor in descriptors), (
        "Regular stdin reads did not use its file descriptor"
    )
    assert stdin.read_calls == 0, "The buffered stdin wrapper was read"


@pytest.mark.skipif(os.name == "nt", reason="regular-file worker is POSIX-specific")
def test_regular_stdin_read_error_uses_controlled_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Raw regular-file read errors return a diagnostic to the shim."""

    class _FailingRegularStdin:
        def __init__(self, descriptor: int) -> None:
            """Expose the file descriptor used by the injected read failure."""
            self._descriptor = descriptor

        def fileno(self) -> int:
            """Return the descriptor passed to the raw-read helper.

            Returns
            -------
            int
                The regular-file descriptor under test.
            """
            return self._descriptor

        def read(self) -> str:
            """Return a sentinel value if the buffered path is used instead.

            Returns
            -------
            str
                A sentinel value that identifies the buffered path.
            """
            return "buffered text"

    def fail_read(_descriptor: int, _size: int) -> typ.NoReturn:
        """Raise an I/O failure from the raw regular-file read.

        Raises
        ------
        _InjectedOSError
            Simulated descriptor read failure.
        """
        raise _InjectedOSError

    with tempfile.TemporaryFile() as regular_file:
        monkeypatch.setattr(sys, "stdin", _FailingRegularStdin(regular_file.fileno()))
        monkeypatch.setattr(shim._shim_stdin.os, "read", fail_read)
        with pytest.raises(SystemExit) as exc:
            shim._shim_stdin._read_stdin_until_eof(
                1.0,
                deadline=None,
                on_error=shim._exit_ipc_error,
            )

    _assert_exit_code(exc, 1)
    assert "IPC error:" in capsys.readouterr().err


def test_regular_stdin_thread_start_error_uses_controlled_exit(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Failure to start the bounded reader does not escape as a traceback."""

    class _FailingThread:
        def __init__(self, **_kwargs: object) -> None:
            """Accept thread construction so the injected start failure is reached."""
            pass

        def start(self) -> typ.NoReturn:
            """Simulate failure to start the bounded stdin worker.

            Raises
            ------
            _InjectedRuntimeError
                Simulated worker startup failure.
            """
            raise _InjectedRuntimeError

    monkeypatch.setattr(shim._shim_stdin.threading, "Thread", _FailingThread)
    with pytest.raises(SystemExit) as exc:
        shim._shim_stdin._read_regular_stdin_until_deadline(
            123,
            1.0,
            shim._exit_ipc_error,
        )

    _assert_exit_code(exc, 1)
    assert "IPC error:" in capsys.readouterr().err


def test_await_regular_stdin_read_rejects_result_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A worker result arriving at the deadline is treated as a timeout."""
    result_queue: queue.Queue[tuple[str, Exception | None]] = queue.Queue()
    result_queue.put(("late input", None))
    monotonic_values = iter([0.0, 1.0])
    monkeypatch.setattr(
        shim._shim_stdin.time,
        "monotonic",
        lambda: next(monotonic_values),
    )

    def raise_error(exc: Exception) -> typ.NoReturn:
        """Propagate the timeout produced for a late worker result."""
        raise exc

    with pytest.raises(TimeoutError, match="timed out reading stdin"):
        shim._shim_stdin._await_regular_stdin_read(result_queue, 1.0, raise_error)


def test_read_buffered_chunk_treats_empty_read_as_eof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A blocking buffered reader reports EOF with an empty byte string."""

    def raise_error(exc: Exception) -> typ.NoReturn:
        """Propagate the read error supplied by the buffered reader."""
        raise exc

    context = shim._shim_stdin._ReadContext(
        descriptor=123,
        poller=shim._shim_stdin._SelectPoller(123),
        deadline=1.0,
        on_error=raise_error,
    )

    monkeypatch.setattr(shim._shim_stdin.time, "monotonic", lambda: 0.0)
    monkeypatch.setattr(
        shim._shim_stdin.os,
        "read",
        lambda *_args: pytest.fail("EOF must not trigger another descriptor read"),
    )

    assert shim._shim_stdin._read_buffered_chunk(context, lambda _size: b"") == b""


def test_read_raw_polled_stdin_reports_descriptor_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Errors from raw descriptor reads use the shim's error policy."""

    class _ReadyPoller:
        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            """Make the raw-read path proceed to the injected descriptor error.

            Returns
            -------
            list[tuple[int, int]]
                One readiness event for the synthetic descriptor.
            """
            return [(123, 1)]

    def raise_error(exc: Exception) -> typ.NoReturn:
        """Propagate descriptor errors through the configured callback."""
        raise exc

    context = shim._shim_stdin._ReadContext(
        descriptor=123,
        poller=_ReadyPoller(),
        deadline=1.0,
        on_error=raise_error,
    )
    monkeypatch.setattr(shim._shim_stdin.time, "monotonic", lambda: 0.0)

    def fail_read(_descriptor: int, _size: int) -> bytes:
        """Raise an I/O error after the fake poller reports readiness.

        Raises
        ------
        _InjectedOSError
            Simulated descriptor read failure.
        """
        raise _InjectedOSError

    monkeypatch.setattr(shim._shim_stdin.os, "read", fail_read)
    with pytest.raises(_InjectedOSError):
        shim._shim_stdin._read_raw_polled_stdin(context)
