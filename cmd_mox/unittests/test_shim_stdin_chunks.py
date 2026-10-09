"""Property tests for decoding stdin delivered in arbitrary byte chunks."""

from __future__ import annotations

import sys
import time
import typing as typ
from itertools import pairwise
from unittest import mock

from hypothesis import given
from hypothesis import strategies as st

from cmd_mox import shim


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


def _read_stdin_chunks(chunks: list[bytes], *, buffered: bool) -> str:
    """Decode byte chunks through one of the shim's polled stdin paths.

    Returns
    -------
    str
        Decoded text with universal newlines translated.
    """

    class _ReadyPoller:
        def poll(self, _timeout: int) -> list[tuple[int, int]]:
            return [(123, 1)]

    class _BufferedReader:
        def __init__(self) -> None:
            self.byte_chunks = iter([*chunks, b""])

        def read1(self, _size: int) -> bytes:
            return next(self.byte_chunks)

    class _Stdin:
        encoding = "utf-8"
        errors = "strict"

        def __init__(self) -> None:
            self.buffer = _BufferedReader()

    def fail_read(exc: Exception) -> typ.NoReturn:
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
