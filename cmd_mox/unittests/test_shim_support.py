"""Shared test doubles, fixtures, and assertions for shim unit tests."""

from __future__ import annotations

import os
import typing as typ

import pytest

if typ.TYPE_CHECKING:
    import collections.abc as cabc


class _DummyStdin:
    """Test double to simulate ``sys.stdin`` behaviour."""

    def __init__(self, data: str, *, is_tty: bool) -> None:
        self._data = data
        self._is_tty = is_tty
        self.read_calls = 0

    def isatty(self) -> bool:
        return self._is_tty

    def read(self) -> str:
        self.read_calls += 1
        return self._data


class _InjectedOSError(OSError):
    """Represent an I/O failure raised by a test double."""


class _InjectedRuntimeError(RuntimeError):
    """Represent a worker failure raised by a test double."""


class _BufferedInput:
    def __init__(
        self,
        chunks: cabc.Iterable[bytes],
        after_read: cabc.Callable[[], None] | None = None,
    ) -> None:
        self._chunks = iter(chunks)
        self._after_read = after_read
        self.read_calls = 0

    def read1(self, _size: int) -> bytes:
        self.read_calls += 1
        if self._after_read is not None:
            self._after_read()
        return next(self._chunks)


class _DescriptorStdin(_DummyStdin):
    encoding = "utf-8"
    errors = "strict"

    def __init__(self, descriptor: int, buffer: _BufferedInput | None = None) -> None:
        super().__init__("", is_tty=False)
        self._descriptor = descriptor
        self.buffer = buffer

    def fileno(self) -> int:
        return self._descriptor


def _assert_exit_code(exc: pytest.ExceptionInfo[BaseException], expected: int) -> None:
    """Assert that *exc* wraps a :class:`SystemExit` with the desired code."""
    err = exc.value
    assert isinstance(err, SystemExit)
    assert err.code == expected


@pytest.fixture
def stdin_pipe_descriptor() -> cabc.Iterator[int]:
    """Yield an owned pipe descriptor for stdin polling tests.

    Yields
    ------
    int
        The read end of the pipe.
    """
    read_descriptor, write_descriptor = os.pipe()
    try:
        yield read_descriptor
    finally:
        os.close(read_descriptor)
        os.close(write_descriptor)


@pytest.fixture
def stdin_pipe_descriptor_at_eof() -> cabc.Iterator[int]:
    """Yield a pipe read descriptor after closing its writer.

    Yields
    ------
    int
        The read end of the pipe, which returns EOF immediately.
    """
    read_descriptor, write_descriptor = os.pipe()
    os.close(write_descriptor)
    try:
        yield read_descriptor
    finally:
        os.close(read_descriptor)
