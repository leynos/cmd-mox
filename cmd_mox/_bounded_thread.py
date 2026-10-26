"""Bounded worker threads for blocking operating-system calls.

Some operating-system calls cannot be interrupted by a timeout. A synchronous
``ReadFile`` on a Windows pipe blocks until data arrives or the peer
disconnects, and reading a stream without a waitable descriptor blocks until
the stream itself chooses to return. Callers run such a call on a daemon
worker and use these helpers to abandon it without leaking the handle it owns.

``CancelSynchronousIo`` is the only supported way to interrupt a read that is
already blocked inside ``ReadFile``; closing the pipe handle does not reliably
wake it. The API is resolved dynamically because it is missing from some
pywin32 builds and from every non-Windows host, where the module stand-ins are
``None``.
"""

from __future__ import annotations

import contextlib
import time
import typing as typ

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import threading

# ``CancelSynchronousIo`` requires THREAD_TERMINATE access to its target.
_THREAD_TERMINATE: typ.Final[int] = 0x0001

# Upper bound on one wait between cancellation attempts. Short enough that a
# request landing between two blocking calls is retried promptly, long enough
# that a cancel which does take effect is not immediately duplicated.
_CANCEL_JOIN_SLICE: typ.Final[float] = 0.05


def stash_outcome[T](
    outcome: dict[str, object], func: cabc.Callable[[], T]
) -> cabc.Callable[[], None]:
    """Build a thread target that stores *func*'s value or exception.

    A worker that raises must not take the process down with it: the failure
    belongs to the caller's thread, which is still deciding whether the result
    is still wanted. Storing both outcomes in *outcome* is what lets the caller
    re-raise verbatim once it has claimed the worker's result.

    Returns
    -------
    collections.abc.Callable[[], None]
        A zero-argument target suitable for :class:`threading.Thread`.
    """

    def run() -> None:
        try:
            outcome["value"] = func()
        except BaseException as exc:  # ruff: ignore[blind-except] - worker-thread boundary: any failure must be stashed and re-raised on the caller's thread
            outcome["error"] = exc

    return run


class _Win32File(typ.Protocol):
    """Subset of the pywin32 file API used by the cancellation helper."""

    def CloseHandle(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self, handle: object
    ) -> None: ...


class _Win32Api(typ.Protocol):
    """Subset of the pywin32 API used by the cancellation helper."""

    def OpenThread(  # ruff: ignore[invalid-function-name] - mirrors pywin32 API casing
        self,
        access: int,
        inherit: bool,  # ruff: ignore[boolean-type-hint-positional-argument] - mirrors pywin32's positional signature
        native_id: int,
    ) -> object: ...


class _PyWinTypes(typ.Protocol):
    """Subset of the pywintypes module used by the cancellation helper."""

    error: type[BaseException]


class HandleCloser:
    """Best-effort guard that closes one Windows handle exactly once.

    Parameters
    ----------
    handle:
        Handle to release.
    win32file:
        Module whose ``CloseHandle`` releases *handle*.
    suppress:
        Exception types ``CloseHandle`` may raise when the handle was already
        closed or its I/O was already aborted.
    """

    __slots__ = ("_closed", "_handle", "_suppress", "_win32file")

    def __init__(
        self,
        handle: object,
        *,
        win32file: _Win32File,
        suppress: tuple[type[BaseException], ...] = (),
    ) -> None:
        self._handle = handle
        self._win32file = win32file
        self._suppress = suppress
        self._closed = False

    def close(self) -> None:
        """Release the handle, ignoring repeat calls and aborted-handle errors."""
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(*self._suppress):
            # Double-close or already-aborted handles may report INVALID_HANDLE;
            # callers only care that resources are reclaimed. ``CloseHandle`` is
            # resolved here rather than on construction so a host without
            # pywin32 fails inside the suppression above.
            self._win32file.CloseHandle(self._handle)


def request_synchronous_io_cancel(
    thread: threading.Thread,
    *,
    win32file: _Win32File | None,
    win32api: _Win32Api | None,
    pywintypes: _PyWinTypes | None,
) -> None:
    """Ask Windows to abort *thread*'s in-flight synchronous I/O.

    Parameters
    ----------
    thread:
        Worker whose native thread handle receives the cancellation.
    win32file:
        Module exposing ``CancelSynchronousIo`` and ``CloseHandle``, or
        ``None`` off Windows.
    win32api:
        Module exposing ``OpenThread``, or ``None`` off Windows.
    pywintypes:
        Module supplying the error type raised by a failed handle close, or
        ``None`` off Windows.
    """
    if win32file is None or win32api is None:
        return
    cancel_io: cabc.Callable[[object], None] | None = getattr(
        win32file, "CancelSynchronousIo", None
    )
    open_thread: cabc.Callable[[int, bool, int], object] | None = getattr(
        win32api, "OpenThread", None
    )
    if cancel_io is None or open_thread is None:
        return
    native_id = thread.native_id
    if native_id is None:
        return
    # Best-effort: failing to cancel is never fatal, because the caller's
    # handle close remains the fallback for waking the worker.
    with contextlib.suppress(Exception):
        # OpenThread(desired_access, inherit_handle, thread_id).
        handle = open_thread(
            _THREAD_TERMINATE,
            False,  # ruff: ignore[boolean-positional-value-in-call] - pywin32 takes the inherit flag positionally
            native_id,
        )
        error_types: tuple[type[BaseException], ...] = ()
        if pywintypes is not None:
            error_types = (pywintypes.error,)
        closer = HandleCloser(handle, win32file=win32file, suppress=error_types)
        # Reuse the shared guard rather than closing by hand, so the thread
        # handle is reclaimed exactly once on both the success and error paths.
        try:
            cancel_io(handle)
        finally:
            closer.close()


def cancel_and_await_worker(
    thread: threading.Thread,
    cancel: cabc.Callable[[], None],
    *,
    grace: float,
    request_cancel: cabc.Callable[[threading.Thread], None],
) -> bool:
    """Abort the worker's I/O and wait, bounded, for the worker to exit.

    Returning before the worker has exited would leave a daemon thread owning
    a handle the caller believes it has released, so the wait is not optional;
    it is merely bounded by *grace*.

    Parameters
    ----------
    thread:
        Worker performing the blocking call.
    cancel:
        Fallback cancellation for the blocking resource, such as closing a
        pipe handle.
    grace:
        Maximum number of seconds to wait for the worker to unwind.
    request_cancel:
        Best-effort native cancellation, such as
        :func:`request_synchronous_io_cancel`.

    Returns
    -------
    bool
        Whether the worker exited before the grace period elapsed.

    Notes
    -----
    *request_cancel* is re-issued across the wait rather than spent once.
    ``CancelSynchronousIo`` reports ``ERROR_NOT_FOUND`` when its target is
    between blocking calls, and a chunked reader decodes between reads, so a
    single attempt can land in that gap and be swallowed. Retrying costs one
    cheap native call per slice and keeps the graceful path reachable.
    """
    deadline = time.monotonic() + grace
    request_cancel(thread)
    cancel()
    while thread.is_alive():
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(min(remaining, _CANCEL_JOIN_SLICE))
        if thread.is_alive():
            request_cancel(thread)
    return not thread.is_alive()
