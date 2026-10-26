"""Unit tests for the Win32 named-pipe adapter's forwarding.

The adapter only runs on Windows, but every method is a single forward to a
``pywin32`` call. Recording stubs stand in for those modules so the forwarding
can be checked anywhere: that each port operation maps to the one Win32 call it
names, with the arguments arriving in the order ``pywin32`` expects.

The two spec-taking calls are checked by positional *identity*, not by value:
the ``CreateNamedPipe`` and ``CreateFile`` specs below give every field a
distinct value or object, so a transposed pair fails the assertion rather than
matching by coincidence. Reusing ``0`` for two integer fields and ``None`` for
both optional handles would hide exactly the mistake these tests exist to
catch. ``test_create_file_detects_a_transposed_field`` pins that property
down, because the guard is otherwise easy to dismantle the next time the spec
changes.

Only the adapter's own forwarding is in scope here. The transport's use of the
port is covered by the named-pipe suites, which drive it through the fake
adapter in :mod:`cmd_mox.unittests._named_pipe_fakes`.
"""

from __future__ import annotations

import typing as typ

from cmd_mox.ipc import _win32_named_pipe_backend as backend_module
from cmd_mox.ipc._win32_named_pipe_backend import Win32NamedPipeBackend
from cmd_mox.ipc.windows import NamedPipeCreateSpec, PipeFileOpenSpec
from cmd_mox.unittests._named_pipe_fakes import FakeOverlapped, FakeWinError

if typ.TYPE_CHECKING:
    import collections.abc as cabc

    import pytest

#: Returned by every stub call so a test can recognise it as the forwarded value.
_SENTINEL: typ.Final[object] = object()

#: Stand-ins for the specs' optional handle fields. They are deliberately two
#: separate objects: sharing one would make ``PipeFileOpenSpec.security_attributes``
#: and ``.template_file`` compare equal, and the ordering assertions would stop
#: noticing if the adapter swapped them.
_SECURITY_ATTRIBUTES: typ.Final[object] = object()
_TEMPLATE_FILE: typ.Final[object] = object()

#: Constants the adapter copies onto itself at construction.
_CONSTANTS: typ.Final[dict[str, dict[str, int]]] = {
    "win32event": {"INFINITE": -1, "WAIT_OBJECT_0": 0, "WAIT_TIMEOUT": 258},
    "win32pipe": {
        "PIPE_ACCESS_DUPLEX": 3,
        "PIPE_TYPE_MESSAGE": 4,
        "PIPE_READMODE_MESSAGE": 2,
        "PIPE_WAIT": 0,
        "PIPE_UNLIMITED_INSTANCES": 255,
    },
    "win32file": {
        "FILE_FLAG_OVERLAPPED": 0x40000000,
        "GENERIC_READ": 0x80000000,
        "GENERIC_WRITE": 0x40000000,
        "OPEN_EXISTING": 3,
    },
}


class _RecordingModule:
    """Stand-in for one ``pywin32`` module that records every attribute use."""

    def __init__(
        self, name: str, calls: list[tuple[str, str, tuple[object, ...]]]
    ) -> None:
        """Bind the module name, the shared call log, and this module's constants."""
        self._name = name
        self._calls = calls
        for constant, value in _CONSTANTS.get(name, {}).items():
            setattr(self, constant, value)

    def __getattr__(self, attribute: str) -> cabc.Callable[..., object]:
        """Return a callable that records the call and yields the sentinel.

        Returns
        -------
        collections.abc.Callable[..., object]
            A stand-in for the named module attribute.
        """

        def record(*args: object) -> object:
            """Log the call and hand back the sentinel every stub returns.

            Returns
            -------
            object
                The shared sentinel, so a test can spot the forwarded value.
            """
            self._calls.append((self._name, attribute, args))
            return _SENTINEL

        return record


class _RecordingPyWinTypes(_RecordingModule):
    """``pywintypes`` stand-in carrying the two types the adapter reads."""

    OVERLAPPED = FakeOverlapped
    error = FakeWinError


class _RecordingWin32File(_RecordingModule):
    """``win32file`` stand-in for the two calls that produce a buffer.

    ``ReadFile`` and ``AllocateReadBuffer`` are not interchangeable: the read
    wraps the caller's own buffer in a ``(status, buffer)`` pair, while the
    allocation mints a fresh buffer of the requested size. One shared handler
    returning a fixed pair would let the adapter get either shape wrong and
    still satisfy the recorded-call assertions, which only look at arguments.
    """

    def __init__(
        self,
        calls: list[tuple[str, str, tuple[object, ...]]],
        read_buffer: cabc.Buffer | None = None,
    ) -> None:
        """Bind the shared call log and the buffer a read hands back."""
        super().__init__("win32file", calls)
        self.read_buffer: cabc.Buffer = (
            read_buffer if read_buffer is not None else bytearray(4)
        )
        #: Set by the last ``AllocateReadBuffer`` call, so a test can check the
        #: adapter returned the buffer the allocation produced.
        self.allocated: bytearray | None = None

    def __getattr__(self, attribute: str) -> cabc.Callable[..., object]:
        """Return the handler for *attribute*, specialised for buffer producers.

        Returns
        -------
        collections.abc.Callable[..., object]
            The read pair, the minted buffer, or the base recording stub.
        """
        if attribute == "ReadFile":
            forwarded = super().__getattr__(attribute)

            def record_read(*args: object) -> tuple[int, cabc.Buffer]:
                """Log the read and wrap the read buffer in its status pair.

                Returns
                -------
                tuple[int, collections.abc.Buffer]
                    The immediate status and the buffer handed to the read.
                """
                forwarded(*args)
                return 0, self.read_buffer

            return record_read
        if attribute == "AllocateReadBuffer":
            forwarded = super().__getattr__(attribute)

            def record_allocation(size: int) -> cabc.Buffer:
                """Log the allocation and mint a zeroed buffer of *size* bytes.

                Returns
                -------
                collections.abc.Buffer
                    The freshly allocated buffer, also kept on ``allocated``.
                """
                forwarded(size)
                self.allocated = bytearray(size)
                return self.allocated

            return record_allocation
        return super().__getattr__(attribute)


class _StubModules:
    """The four ``pywin32`` modules plus the calls recorded across all of them."""

    def __init__(self, read_buffer: cabc.Buffer | None = None) -> None:
        """Build the four stand-ins sharing one call log."""
        self.calls: list[tuple[str, str, tuple[object, ...]]] = []
        self.win32event = _RecordingModule("win32event", self.calls)
        self.win32pipe = _RecordingModule("win32pipe", self.calls)
        self.win32file = _RecordingWin32File(self.calls, read_buffer)
        self.pywintypes = _RecordingPyWinTypes("pywintypes", self.calls)

    def install(self, monkeypatch: pytest.MonkeyPatch) -> Win32NamedPipeBackend:
        """Bind these stubs where the adapter imports its modules.

        Returns
        -------
        Win32NamedPipeBackend
            An adapter whose operations reach these stubs.
        """
        for name in ("win32event", "win32pipe", "win32file", "pywintypes"):
            monkeypatch.setattr(backend_module, name, getattr(self, name))
        return Win32NamedPipeBackend()


def _create_spec() -> NamedPipeCreateSpec:
    """Build a creation spec whose eight fields are all distinguishable.

    Returns
    -------
    NamedPipeCreateSpec
        A spec no two of whose fields compare equal.
    """
    return NamedPipeCreateSpec(
        name=r"\\.\pipe\cmdmox-1",
        open_mode=3,
        pipe_mode=4,
        max_instances=255,
        out_buffer_size=4096,
        in_buffer_size=8192,
        default_timeout=1000,
        security_attributes=_SECURITY_ATTRIBUTES,
    )


def _open_spec() -> PipeFileOpenSpec:
    """Build an open spec whose seven fields are all distinguishable.

    Returns
    -------
    PipeFileOpenSpec
        A spec no two of whose fields compare equal.
    """
    return PipeFileOpenSpec(
        file_name=r"\\.\pipe\cmdmox-1",
        desired_access=0xC0000000,
        share_mode=0,
        security_attributes=_SECURITY_ATTRIBUTES,
        creation_disposition=3,
        flags_and_attributes=0x40000000,
        template_file=_TEMPLATE_FILE,
    )


def test_construction_binds_the_modules_and_constants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Construction copies every constant and module view onto the instance.

    The expected values come from ``_CONSTANTS``, and the instance's own
    upper-case integer attributes are compared against that table as whole
    dictionaries. That catches both directions at once: a constant the table
    expects but the adapter stopped binding, and one the adapter binds but the
    table never learned about. The second matters — every bound constant is
    read by :mod:`cmd_mox.ipc.named_pipe` when it builds its Win32 arguments,
    so a new one would otherwise go untested and surface as an
    ``AttributeError`` on Windows only.
    """
    modules = _StubModules()

    backend = modules.install(monkeypatch)

    expected = {
        name: value
        for constants in _CONSTANTS.values()
        for name, value in constants.items()
    }
    bound = {
        name: value
        for name, value in vars(backend).items()
        if isinstance(value, int) and name.isupper()
    }
    assert bound == expected, "the adapter's bound constants differ from the table"
    assert backend.win32file is modules.win32file, "the file view was not exposed"
    assert backend.pywintypes is modules.pywintypes, "the types view was not exposed"
    assert backend.error is FakeWinError, "the error type was not exposed"
    assert not modules.calls, "construction issued a Win32 call of its own"


def test_create_event_requests_a_manual_reset_unnamed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The shutdown event is manual-reset, initially clear, and unnamed."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)

    assert backend.create_event() is _SENTINEL, "the handle was not returned"
    assert modules.calls == [("win32event", "CreateEvent", (None, True, False, None))]


def test_set_event_forwards_the_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    """Signalling forwards the handle unchanged."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)
    handle = object()

    backend.set_event(handle)

    assert modules.calls == [("win32event", "SetEvent", (handle,))]


def test_wait_watches_every_handle_without_waiting_for_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The wait returns on the first signalled handle and passes its timeout."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)
    first, second = object(), object()

    result = backend.wait((first, second), 250)

    assert result is _SENTINEL, "the wait result was not returned"
    assert modules.calls == [
        ("win32event", "WaitForMultipleObjects", ([first, second], False, 250))
    ]


def test_create_overlapped_pairs_the_structure_with_its_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The overlapped structure carries the event the transport will await."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)

    overlapped, event = backend.create_overlapped()

    assert isinstance(overlapped, FakeOverlapped), "the structure was not built"
    assert overlapped.hEvent is event, "the event was not stored on the structure"
    assert event is _SENTINEL, "the event handle was not returned"


def test_get_overlapped_result_waits_for_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collecting a result blocks until the operation has finished."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)
    handle, overlapped = object(), object()

    result = backend.get_overlapped_result(handle, overlapped)

    assert result is _SENTINEL, "the byte count was not returned"
    assert modules.calls == [
        ("win32file", "GetOverlappedResult", (handle, overlapped, True))
    ]


def test_cancel_io_names_the_handle_and_overlapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancellation targets the operation the overlapped describes."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)
    handle, overlapped = object(), object()

    backend.cancel_io(handle, overlapped)

    assert modules.calls == [("win32file", "CancelIoEx", (handle, overlapped))]


def test_create_named_pipe_unpacks_the_spec_in_call_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All eight ``CreateNamedPipe`` arguments arrive positionally, in order."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)
    spec = _create_spec()

    assert backend.create_named_pipe(spec) is _SENTINEL, "the handle was not returned"
    assert modules.calls == [
        (
            "win32pipe",
            "CreateNamedPipe",
            (spec.name, 3, 4, 255, 4096, 8192, 1000, _SECURITY_ATTRIBUTES),
        )
    ]


def test_connect_named_pipe_forwards_the_overlapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connect is issued asynchronously against the overlapped."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)
    handle, overlapped = object(), object()

    result = backend.connect_named_pipe(handle, overlapped)

    assert result is _SENTINEL, "the immediate status was not returned"
    assert modules.calls == [("win32pipe", "ConnectNamedPipe", (handle, overlapped))]


def test_disconnect_named_pipe_forwards_the_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disconnecting passes only the pipe instance."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)
    handle = object()

    backend.disconnect_named_pipe(handle)

    assert modules.calls == [("win32pipe", "DisconnectNamedPipe", (handle,))]


def test_read_file_passes_the_buffer_and_overlapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read forwards the writable buffer and its overlapped structure."""
    buffer: cabc.Buffer = bytearray(4)
    modules = _StubModules(buffer)
    backend = modules.install(monkeypatch)
    handle, overlapped = object(), object()

    status, received = backend.read_file(handle, buffer, overlapped)

    assert status == 0, "the immediate read status was not returned"
    assert received is buffer, "the filled buffer was not returned"
    assert modules.calls == [("win32file", "ReadFile", (handle, buffer, overlapped))]


def test_allocate_read_buffer_requests_the_chunk_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Buffer allocation asks for exactly the requested capacity.

    The returned buffer is the one the allocation produced, not a read's pair:
    the port promises a bare buffer here, and only the read returns a tuple.
    """
    modules = _StubModules()
    backend = modules.install(monkeypatch)

    buffer = backend.allocate_read_buffer(4096)

    assert modules.calls == [("win32file", "AllocateReadBuffer", (4096,))]
    assert buffer is modules.win32file.allocated, (
        "the adapter did not return the buffer the allocation produced"
    )
    assert len(buffer) == 4096, "the buffer was not sized to the request"


def test_create_file_unpacks_the_spec_in_call_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All seven ``CreateFile`` arguments arrive positionally, in order."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)
    spec = _open_spec()

    assert backend.create_file(spec) is _SENTINEL, "the handle was not returned"
    assert modules.calls == [
        (
            "win32file",
            "CreateFile",
            (
                spec.file_name,
                0xC0000000,
                0,
                _SECURITY_ATTRIBUTES,
                3,
                0x40000000,
                _TEMPLATE_FILE,
            ),
        )
    ]


def test_close_handle_forwards_the_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closing passes only the handle to release."""
    modules = _StubModules()
    backend = modules.install(monkeypatch)
    handle = object()

    backend.close_handle(handle)

    assert modules.calls == [("win32file", "CloseHandle", (handle,))]


def test_create_file_detects_a_transposed_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A swap of two same-typed spec fields reaches the call site visibly.

    ``share_mode`` and ``flags_and_attributes`` are both plain integers, and
    ``security_attributes`` and ``template_file`` are both bare objects. If the
    fixtures reused one value for each pair, ``create_file`` could transpose
    them and still satisfy its ordering assertion. This pins the guard: the
    open spec is rebuilt with the two integers exchanged, and the call the
    adapter makes must carry the exchanged values in their original positions.

    The assertion is exact rather than an inequality, so an adapter that drops
    the call entirely — or makes no call at all — fails here too. Only one
    ``CreateFile`` call is expected; any other call is a failure as well.
    """
    modules = _StubModules()
    backend = modules.install(monkeypatch)
    spec = _open_spec()
    transposed = PipeFileOpenSpec(
        file_name=spec.file_name,
        desired_access=spec.desired_access,
        share_mode=spec.flags_and_attributes,
        security_attributes=spec.security_attributes,
        creation_disposition=spec.creation_disposition,
        flags_and_attributes=spec.share_mode,
        template_file=spec.template_file,
    )

    backend.create_file(transposed)

    assert modules.calls == [
        (
            "win32file",
            "CreateFile",
            (
                spec.file_name,
                0xC0000000,
                0x40000000,
                _SECURITY_ATTRIBUTES,
                3,
                0,
                _TEMPLATE_FILE,
            ),
        )
    ], "the transposed share_mode did not reach CreateFile in position"
