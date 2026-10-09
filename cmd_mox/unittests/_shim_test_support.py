"""Shared test doubles and assertions for shim unit tests."""

from __future__ import annotations

import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    import pytest


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
    """Assert that *exc* wraps a :class:`SystemExit` with the desired code.

    Raises
    ------
    TypeError
        If the wrapped exception is not a :class:`SystemExit`.
    AssertionError
        If the wrapped exit code does not match *expected*.
    """
    err = exc.value
    if not isinstance(err, SystemExit):
        msg = "Expected a SystemExit exception"
        raise TypeError(msg)
    if err.code != expected:
        msg = f"Expected exit code {expected}, got {err.code}"
        raise AssertionError(msg)
