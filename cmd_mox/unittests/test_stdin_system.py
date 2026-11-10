"""Unit tests for the narrow system boundary used by stdin capture."""

from __future__ import annotations

import os
import select
import types
import typing as typ

import pytest

from cmd_mox import _path_utils as path_utils
from cmd_mox import _stdin_system as stdin_system
from cmd_mox.unittests._stdin_capture_fakes import _fail_poll, _RecordingNativePoller

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    from pathlib import Path

_HANDLE = object()
_NEEDS_POLL = pytest.mark.skipif(
    not hasattr(select, "poll"), reason="the native poller requires select.poll"
)
_POSIX_PIPE = pytest.mark.skipif(
    os.name == "nt", reason="select cannot watch a Windows CRT pipe"
)


class _DescriptorStream:
    """Stream double exposing a fixed descriptor."""

    def __init__(self, descriptor: int) -> None:
        """Store the descriptor reported by ``fileno``."""
        self._descriptor = descriptor

    def fileno(self) -> int:
        """Return the stored descriptor.

        Returns
        -------
        int
            The descriptor configured for the double.
        """
        return self._descriptor


class _RecordingSelect:
    """``select.select`` double recording every requested wait."""

    def __init__(self, *, readable: bool = True) -> None:
        """Configure whether the watched descriptor reports readiness."""
        self._readable = readable
        self.timeouts: list[float] = []

    def __call__(
        self,
        readers: cabc.Sequence[int],
        _writers: cabc.Sequence[int],
        _errors: cabc.Sequence[int],
        timeout: float,
    ) -> tuple[list[int], list[int], list[int]]:
        """Record the wait and report *readers* according to the configuration.

        Returns
        -------
        tuple[list[int], list[int], list[int]]
            Readable descriptors, and empty writable/error lists.
        """
        self.timeouts.append(timeout)
        return (list(readers) if self._readable else []), [], []


class _RejectingSelect:
    """``select.select`` double that rejects the descriptor."""

    @staticmethod
    def __call__(
        _readers: cabc.Sequence[int],
        _writers: cabc.Sequence[int],
        _errors: cabc.Sequence[int],
        _timeout: float,
    ) -> typ.NoReturn:
        """Raise the ``ValueError`` ``select`` uses for unwatchable descriptors.

        Raises
        ------
        ValueError
            Always, so poller creation must reject the adapter.
        """
        msg = "file descriptor out of range"
        raise ValueError(msg)


class _NoFileno:
    """Stream without a descriptor, standing in for an in-memory double."""

    @staticmethod
    def fileno() -> int:
        """Reject descriptor access.

        Raises
        ------
        AttributeError
            Always, matching a ``StringIO`` dereference.
        """
        msg = "no descriptor"
        raise AttributeError(msg)


class _BrokenMsvcrt:
    """``msvcrt`` double whose handle resolution fails."""

    @staticmethod
    def get_osfhandle(_descriptor: int) -> typ.NoReturn:
        """Fail the CRT handle lookup.

        Raises
        ------
        OSError
            Always, so handle resolution reports no handle.
        """
        msg = "invalid descriptor"
        raise OSError(msg)


def _import_module_failing_on(module_name: str) -> cabc.Callable[[str], object]:
    """Return an importer that fails when *module_name* is requested.

    Returns
    -------
    cabc.Callable[[str], object]
        Importer returning a stand-in for every other module name.
    """

    def import_module(name: str) -> object:
        """Return a stand-in unless *name* is the configured failure.

        Returns
        -------
        object
            A stand-in for any module other than the configured failure.

        Raises
        ------
        ImportError
            When *name* matches the configured missing module.
        """
        if name == module_name:
            msg = name
            raise ImportError(msg)
        return object()

    return import_module


# --- select adapter ---------------------------------------------------------


def test_select_poller_converts_milliseconds_to_seconds() -> None:
    """The adapter passes the remaining deadline to ``select`` in seconds."""
    select_call = _RecordingSelect()

    events = stdin_system._SelectPoller(3, select_call).poll(2_500)

    assert select_call.timeouts == [2.5], "poll timeout was not converted to seconds"
    assert events == [(3, 1)], "readiness was not reported as an event"


def test_select_poller_reports_no_event_without_readiness() -> None:
    """An unready descriptor produces no synthetic event."""
    poller = stdin_system._SelectPoller(3, _RecordingSelect(readable=False))

    assert poller.poll(0) == [], "an unready descriptor reported readiness"


def test_select_poller_propagates_rejection() -> None:
    """A descriptor ``select`` cannot watch stays a ``ValueError``."""
    poller = stdin_system._SelectPoller(3, _RejectingSelect())

    with pytest.raises(ValueError, match="out of range"):
        poller.poll(0)


@_POSIX_PIPE
def test_select_poller_defaults_to_the_select_module() -> None:
    """The adapter falls back to the stdlib ``select.select`` call."""
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"ready")
        events = stdin_system._SelectPoller(read_fd).poll(0)
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert events == [(read_fd, 1)], "the default select call missed readiness"


# --- poller selection -------------------------------------------------------


@_NEEDS_POLL
def test_create_stdin_poller_prefers_the_native_poller() -> None:
    """An accepted native poller is returned with a zero-timeout probe."""
    poller = _RecordingNativePoller()

    created = stdin_system._create_stdin_poller(7, native_poll=lambda: poller)

    assert created is poller, "the native poller was not selected"
    assert poller.registered == [(7, select.POLLIN | select.POLLHUP)], (
        "the descriptor was not registered for readable and hang-up events"
    )
    assert poller.timeouts == [0], "the native poller was not probed immediately"


def test_create_stdin_poller_falls_back_when_native_poll_fails() -> None:
    """A native poller failure selects the ``select`` adapter."""
    select_call = _RecordingSelect()

    created = stdin_system._create_stdin_poller(
        7, native_poll=_fail_poll, select_call=select_call
    )

    assert isinstance(created, stdin_system._SelectPoller), (
        "the select adapter was not selected"
    )
    assert select_call.timeouts == [0], "the adapter was not probed immediately"


@_NEEDS_POLL
def test_create_stdin_poller_rejects_a_native_poller_reporting_pollnval() -> None:
    """A probe reporting ``POLLNVAL`` rejects the native poller."""
    poller = _RecordingNativePoller(events=[(7, select.POLLNVAL)])
    select_call = _RecordingSelect()

    created = stdin_system._create_stdin_poller(
        7, native_poll=lambda: poller, select_call=select_call
    )

    assert isinstance(created, stdin_system._SelectPoller), (
        "an invalid descriptor was accepted by the native poller"
    )


def test_create_stdin_poller_returns_none_when_neither_mechanism_works() -> None:
    """`None` tells the planner to use the tracked direct-read fallback."""
    created = stdin_system._create_stdin_poller(
        7, native_poll=_fail_poll, select_call=_RejectingSelect()
    )

    assert created is None, "an unwatchable descriptor produced a poller"


# --- Windows module and handle resolution -----------------------------------


def test_windows_modules_are_inert_off_windows() -> None:
    """A non-Windows host never imports the pywin32 modules."""
    imported: list[str] = []

    def import_module(name: str) -> object:
        """Record an import attempt and return a stand-in module."""
        imported.append(name)
        return object()

    modules = stdin_system._windows_modules(
        is_windows=lambda: False, import_module=import_module
    )

    assert modules is None, "a non-Windows host reported pywin32 modules"
    assert imported == [], "a non-Windows host imported pywin32"


def test_windows_modules_import_in_read_cancel_and_error_order() -> None:
    """The module triple is ordered as read, cancel, and error handling."""
    imported: list[str] = []

    def import_module(name: str) -> object:
        """Record each module import and return a name-tagged stand-in."""
        imported.append(name)
        return name

    modules = stdin_system._windows_modules(
        is_windows=lambda: True, import_module=import_module
    )

    assert modules == ("win32file", "win32api", "pywintypes"), (
        "the modules were not returned in capture order"
    )
    assert imported == ["win32file", "win32api", "pywintypes"], (
        "the modules were imported out of order"
    )


@pytest.mark.parametrize("missing", ["win32file", "win32api", "pywintypes"])
def test_windows_modules_are_absent_when_pywin32_is_incomplete(missing: str) -> None:
    """A partial pywin32 install degrades to the fallback."""
    modules = stdin_system._windows_modules(
        is_windows=lambda: True,
        import_module=_import_module_failing_on(missing),
    )

    assert modules is None, f"a missing {missing} still reported modules"


def test_stdin_handle_resolves_through_msvcrt() -> None:
    """The CRT descriptor is converted to the native handle."""
    msvcrt = types.SimpleNamespace(get_osfhandle=lambda descriptor: _HANDLE)

    handle = stdin_system._stdin_handle(
        _DescriptorStream(4), import_module=lambda _name: msvcrt
    )

    assert handle is _HANDLE, "the native handle was not returned"


def test_stdin_handle_reports_no_handle_without_a_descriptor() -> None:
    """A stream with no descriptor resolves to no Windows handle."""
    msvcrt = types.SimpleNamespace(get_osfhandle=lambda descriptor: _HANDLE)

    handle = stdin_system._stdin_handle(_NoFileno(), import_module=lambda _name: msvcrt)

    assert handle is None, "a stream without a descriptor produced a handle"


@pytest.mark.parametrize(
    "import_module",
    [
        pytest.param(_import_module_failing_on("msvcrt"), id="missing-msvcrt"),
        pytest.param(lambda _name: _BrokenMsvcrt(), id="handle-lookup-failure"),
    ],
)
def test_stdin_handle_reports_no_handle_on_failure(
    import_module: cabc.Callable[[str], object],
) -> None:
    """An unavailable CRT or rejected lookup degrades to no handle."""
    handle = stdin_system._stdin_handle(
        _DescriptorStream(4), import_module=import_module
    )

    assert handle is None, "a failed handle lookup was not contained"


# --- production system ------------------------------------------------------


def test_production_system_is_shared() -> None:
    """Every default capture shares the stateless production system."""
    assert stdin_system.system_stdin() is stdin_system.SYSTEM_STDIN, (
        "the production system is not shared"
    )


def test_production_system_reports_the_live_platform() -> None:
    """The Windows half reads the platform flag on every access."""
    system = stdin_system._OsWindowsStdinSystem()

    assert system.is_windows == path_utils.IS_WINDOWS, (
        "the Windows flag was not read from the live platform"
    )


def test_production_system_reads_a_descriptor() -> None:
    """Raw descriptor reads return the bytes written by the peer."""
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, b"payload")
        os.close(write_fd)
        data = stdin_system.SYSTEM_STDIN.read(read_fd, 16)
    finally:
        os.close(read_fd)

    assert data == b"payload", "the production system did not read the descriptor"


def test_production_system_detects_regular_files(tmp_path: Path) -> None:
    """Regular files and pipes are classified for strategy selection."""
    path = tmp_path / "input.txt"
    path.write_text("data", encoding="utf-8")
    read_fd, write_fd = os.pipe()
    try:
        with path.open(encoding="utf-8") as regular_file:
            assert stdin_system.SYSTEM_STDIN.is_regular_file(regular_file.fileno()), (
                "a regular file was not recognized"
            )
        assert not stdin_system.SYSTEM_STDIN.is_regular_file(read_fd), (
            "a pipe was classified as a regular file"
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_production_system_treats_an_unstattable_descriptor_as_pollable() -> None:
    """A descriptor that cannot be queried keeps its polled strategy."""
    assert not stdin_system.SYSTEM_STDIN.is_regular_file(-1), (
        "an unstattable descriptor was classified as a regular file"
    )


def test_production_system_creates_an_unstarted_daemon_worker() -> None:
    """Worker creation leaves starting the thread to the capture."""
    worker = stdin_system.SYSTEM_STDIN.create_worker(lambda: None, name="stdin-read")

    assert worker.daemon is True, "the stdin worker is not a daemon"
    assert worker.name == "stdin-read", "the stdin worker lost its name"
    assert worker.ident is None, "the stdin worker was started eagerly"


def test_production_system_creates_a_signalling_event() -> None:
    """Completion events start clear and report a completed set."""
    event = stdin_system.SYSTEM_STDIN.create_event()

    assert event.wait(0) is False, "a fresh event reported completion"
    event.set()
    assert event.wait(0) is True, "a set event did not report completion"
