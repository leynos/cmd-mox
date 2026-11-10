r"""Property tests for chunk-boundary decoding in bounded stdin capture.

Each read strategy stores the decoded prefix of every chunk it receives, so a
byte split that lands inside a UTF-8 sequence or between ``\\r`` and ``\\n``
must not change the captured text. These properties drive the strategy loops
with arbitrary byte boundaries rather than with one hand-picked corpus.

The reference text is computed directly from the generated characters, not by
reusing the capture decoder, so a decoder that mistranslates every chunk
cannot hide behind a matching oracle.
"""

from __future__ import annotations

import itertools
import os
import types
import typing as typ

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cmd_mox import _stdin_capture as capture
from cmd_mox._stdin_system import SYSTEM_STDIN, StdinSystem
from cmd_mox.unittests._shim_test_support import (
    _BufferedInput,
    _DescriptorStdin,
)
from cmd_mox.unittests._stdin_capture_fakes import (
    _Clock,
    _DescriptorStream,
    _SystemDouble,
)

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_POSIX_ONLY = pytest.mark.skipif(
    os.name == "nt", reason="the injected POSIX reader doubles are POSIX-only"
)

# Surrogates cannot be encoded as UTF-8, so they cannot appear in a payload.
_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=24,
)
# Fractions are mapped onto the payload length, keeping generated cut points
# meaningful for both empty and multi-byte payloads.
_SPLITS = st.lists(
    st.floats(
        min_value=0.0,
        max_value=1.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    max_size=8,
)


class _ReadyPoller:
    """Poller that reports stdin as readable without ever waiting."""

    def poll(self, _timeout: int, /) -> list[tuple[int, int]]:
        """Return one readiness event.

        Returns
        -------
        list[tuple[int, int]]
            A single readable event, so the strategy never blocks.
        """
        return [(0, 1)]


def _split(payload: bytes, fractions: cabc.Iterable[float]) -> list[bytes]:
    """Cut *payload* at the generated boundaries.

    Returns
    -------
    list[bytes]
        Non-empty chunks that concatenate back to *payload*.
    """
    points = sorted({0, len(payload), *(int(f * len(payload)) for f in fractions)})
    return [
        payload[start:stop]
        for start, stop in itertools.pairwise(points)
        if stop > start
    ]


def _expected(text: str, *, translate_newlines: bool) -> str:
    """Return the text the capture decoder contract produces for *text*.

    Returns
    -------
    str
        *text* unchanged when newline translation is off, or with newlines
        normalised when it is on, matching ``TextIOWrapper``.
    """
    if translate_newlines:
        return text.replace("\r\n", "\n").replace("\r", "\n")
    return text


def _scripted(
    chunks: list[bytes], eof: bytes = b""
) -> cabc.Callable[[int, int], bytes]:
    """Return a chunk reader that replays *chunks* and then *eof*.

    Returns
    -------
    cabc.Callable[[int, int], bytes]
        Reader matching the ``(descriptor_or_handle, size)`` call shape.
    """
    replay = iter([*chunks, eof])

    def read(_source: object, _size: int) -> bytes:
        """Return the next scripted chunk.

        Returns
        -------
        bytes
            The next chunk, or the EOF marker once the chunks are exhausted.
        """
        return next(replay)

    return read


def _capture(
    plan: capture._Plan,
    *,
    stream: capture._StdinStream,
    clock: _Clock,
    system: StdinSystem = SYSTEM_STDIN,
) -> capture._Capture:
    """Build a capture whose deadline is far beyond the fake clock.

    Returns
    -------
    capture._Capture
        Capture bound to *plan*, *stream* and *system* under *clock*.
    """
    return capture._Capture(
        stream=stream,
        timeout=1.0,
        deadline=1.0,
        clock=clock,
        plan=plan,
        system=system,
    )


@_POSIX_ONLY
@settings(max_examples=40, deadline=None)
@given(text=_TEXT, splits=_SPLITS)
def test_regular_file_decode_is_independent_of_chunk_boundaries(
    text: str,
    splits: list[float],
) -> None:
    """Regular-file reads decode a payload identically for every byte split."""
    chunks = _split(text.encode(), splits)
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    # A per-example system keeps the injected read local to each input; the
    # fixture form is not reset between Hypothesis examples.
    system = _SystemDouble(read_override=_scripted(chunks))
    try:
        result = capture._read_regular_file(
            _capture(
                capture._Plan("regular", descriptor=read_fd),
                stream=_DescriptorStream(read_fd),
                clock=_Clock(),
                system=system,
            )
        )
    finally:
        os.close(read_fd)

    assert result == _expected(text, translate_newlines=False), (
        "chunk boundaries changed the decoded text"
    )


@_POSIX_ONLY
@settings(max_examples=40, deadline=None)
@given(text=_TEXT, splits=_SPLITS)
def test_raw_polled_decode_is_independent_of_chunk_boundaries(
    text: str,
    splits: list[float],
) -> None:
    """Raw polled reads decode a payload identically for every byte split."""
    chunks = _split(text.encode(), splits)
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    system = _SystemDouble(read_override=_scripted(chunks))
    try:
        result = capture._read_raw_polled(
            _capture(
                capture._Plan("poll", descriptor=read_fd, poller=_ReadyPoller()),
                stream=_DescriptorStream(read_fd),
                clock=_Clock(),
                system=system,
            ),
            capture._PollContext(read_fd, _ReadyPoller(), 1.0, 1.0, _Clock()),
        )
    finally:
        os.close(read_fd)

    assert result == _expected(text, translate_newlines=False), (
        "chunk boundaries changed the decoded text"
    )


@_POSIX_ONLY
@settings(max_examples=40, deadline=None)
@given(text=_TEXT, splits=_SPLITS)
def test_buffered_decode_is_independent_of_chunk_boundaries(
    text: str,
    splits: list[float],
) -> None:
    """Buffered reads decode a payload identically for every byte split."""
    chunks = _split(text.encode(), splits)
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    stream = _DescriptorStdin(read_fd, buffer=_BufferedInput([*chunks, b""]))
    try:
        result = capture._read_polled(
            _capture(
                capture._Plan("poll", descriptor=read_fd, poller=_ReadyPoller()),
                stream=stream,
                clock=_Clock(),
            )
        )
    finally:
        os.close(read_fd)

    assert result == _expected(text, translate_newlines=False), (
        "chunk boundaries changed the decoded text"
    )


@settings(max_examples=40, deadline=None)
@given(text=_TEXT, splits=_SPLITS)
def test_windows_handle_decode_is_independent_of_chunk_boundaries(
    text: str,
    splits: list[float],
) -> None:
    """Windows handle reads decode a payload identically for every byte split."""
    chunks = _split(text.encode(), splits)
    replay = iter([*chunks, b""])
    win32file = types.SimpleNamespace(ReadFile=lambda _handle, _size: (0, next(replay)))
    stream = _DescriptorStream(0)

    result = capture._read_win32_until_eof(
        typ.cast("capture._Win32File", win32file),
        typ.cast("capture._bounded_thread._PyWinTypes", types.SimpleNamespace()),
        object(),
        capture._stdin_decoder(stream, translate_newlines=os.name == "nt"),
    )

    assert result == _expected(text, translate_newlines=os.name == "nt"), (
        "chunk boundaries changed the decoded text"
    )
