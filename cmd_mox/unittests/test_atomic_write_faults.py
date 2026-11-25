"""Fault-injection tests for the internal atomic write protocol."""

from __future__ import annotations

import dataclasses as dc
import errno
import importlib
import json
import os
import sys
import threading
import typing as typ
from pathlib import Path

import pytest

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import types

#: One injected fault per protocol stage. The kinds are ``before`` (raise
#: instead of doing the work), ``after`` (do the work, then raise), and ``half``
#: (write a truncated prefix, then raise). The table is exhaustive by design:
#: ``test_fault_table_covers_every_stage`` compares its keys with the stage
#: enumeration, so a new stage without a fault fails the suite.
_FAULT_SPECS: dict[str, tuple[str, str]] = {
    "serialize": ("_serialize_json", "before"),
    "prepare_directory": ("_make_parent", "before"),
    "create_temporary": ("_create_temporary", "before"),
    "apply_mode": ("_apply_mode", "after"),
    "write": ("_write_all", "half"),
    "sync_file": ("_fsync_descriptor", "after"),
    "close_temporary": ("_close_temporary", "before"),
    "replace": ("_replace", "before"),
    "sync_directory": ("_fsync_directory", "before"),
}
_PRE_REPLACE_STAGE_NAMES = tuple(
    name for name in _FAULT_SPECS if name != "sync_directory"
)
_PRIOR_STATES = ("absent", "present", "orphaned")
_PREVIOUS_DOCUMENT = b'{\n  "previous": true\n}\n'
_ORPHAN_NAME = ".fixture.json.orphan.tmp"


def _atomic_module() -> types.ModuleType:
    """Return the internal atomic write module under test."""
    return importlib.import_module("cmd_mox._atomic_write")


def _listing(directory: Path) -> set[str]:
    """Return the exact entry names of *directory*."""
    return {entry.name for entry in directory.iterdir()}


def _assert_preserved(target: Path, previous: bytes | None, listing: set[str]) -> None:
    """Assert the target and its directory are exactly as they were."""
    if previous is None:
        assert not target.exists(), "an absent target must stay absent"
    else:
        assert target.read_bytes() == previous, "the previous document must survive"
    assert _listing(target.parent) == listing, "no residue may remain"


def _raise_before(exc: BaseException) -> cabc.Callable[..., typ.NoReturn]:
    """Build a seam replacement that raises *exc* without doing any work."""

    def wrapper(*args: object, **kwargs: object) -> typ.NoReturn:
        """Raise the injected failure."""
        raise exc

    return wrapper


def _raise_after(
    real: cabc.Callable[..., object], exc: BaseException
) -> cabc.Callable[..., typ.NoReturn]:
    """Build a seam replacement that runs *real* and then raises *exc*."""

    def wrapper(*args: object, **kwargs: object) -> typ.NoReturn:
        """Perform the real work, then raise the injected failure."""
        real(*args, **kwargs)
        raise exc

    return wrapper


def _write_half(
    real: cabc.Callable[..., object], exc: BaseException
) -> cabc.Callable[..., typ.NoReturn]:
    """Build a write replacement that commits half the bytes, then fails."""

    def wrapper(fd: int, data: bytes) -> typ.NoReturn:
        """Write a truncated prefix, then raise the injected failure."""
        real(fd, data[: len(data) // 2])
        raise exc

    return wrapper


def _fault_wrapper(
    real: cabc.Callable[..., object], kind: str, exc: BaseException
) -> cabc.Callable[..., typ.NoReturn]:
    """Build the seam replacement for one fault kind."""
    if kind == "before":
        return _raise_before(exc)
    if kind == "half":
        return _write_half(real, exc)
    return _raise_after(real, exc)


def _install_fault(
    monkeypatch: pytest.MonkeyPatch,
    module: types.ModuleType,
    stage_name: str,
    exc: BaseException,
) -> None:
    """Patch the seam for *stage_name* so it fails with *exc*."""
    seam_name, kind = _FAULT_SPECS[stage_name]
    real = getattr(module, seam_name)
    monkeypatch.setattr(module, seam_name, _fault_wrapper(real, kind, exc))


def _skip_durability(monkeypatch: pytest.MonkeyPatch, module: types.ModuleType) -> None:
    """Replace the durability call with a no-op to keep protocol tests fast."""
    monkeypatch.setattr(module, "_fsync_descriptor", lambda _fd: None)


def _descriptors_under(directory: Path) -> set[str]:
    """Return the open descriptors of this process that point into *directory*."""
    descriptor_dir = Path("/proc/self/fd")
    if not descriptor_dir.is_dir():
        pytest.skip("/proc/self/fd is unavailable on this platform")
    resolved = str(directory.resolve())
    found: set[str] = set()
    for entry in descriptor_dir.iterdir():
        try:
            target = str(entry.readlink())
        except OSError:
            continue
        target = target.removesuffix(" (deleted)")
        if target.startswith(resolved + os.sep):
            found.add(target)
    return found


@dc.dataclass(slots=True)
class _PairRun:
    """State for one ordered pair of writes against a single target."""

    module: types.ModuleType
    target: Path
    committed: object

    def apply(self, payload: object, stage_name: str | None) -> None:
        """Run one success or injected fault, recording what committed."""
        if stage_name is None:
            self.module.atomic_write_json(self.target, payload)
            self.committed = payload
            return
        exc = OSError(errno.EIO, "injected failure")
        with pytest.MonkeyPatch.context() as monkeypatch:
            _install_fault(monkeypatch, self.module, stage_name, exc)
            with pytest.raises(self.module.AtomicWriteError) as excinfo:
                self.module.atomic_write_json(self.target, payload)
        assert excinfo.value.__cause__ is exc, "the injected fault must fire"
        if stage_name == "sync_directory":
            self.committed = payload


def test_fault_table_covers_every_stage() -> None:
    """Every protocol stage has exactly one seam and one injected fault."""
    module = _atomic_module()
    stages = {member.value for member in module.AtomicWriteStage}

    assert set(_FAULT_SPECS) == stages
    for seam_name, kind in _FAULT_SPECS.values():
        assert kind in {"before", "after", "half"}
        assert callable(getattr(module, seam_name))


@pytest.mark.parametrize("stage_name", _PRE_REPLACE_STAGE_NAMES)
@pytest.mark.parametrize("prior_state", _PRIOR_STATES)
def test_failure_before_replacement_preserves_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage_name: str,
    prior_state: str,
) -> None:
    """A failure before the replace leaves the target and directory untouched."""
    module = _atomic_module()
    target = tmp_path / "fixture.json"
    previous: bytes | None = None
    if prior_state != "absent":
        target.write_bytes(_PREVIOUS_DOCUMENT)
        previous = _PREVIOUS_DOCUMENT
    if prior_state == "orphaned":
        (tmp_path / _ORPHAN_NAME).write_bytes(b"stale")
    listing = _listing(tmp_path)
    exc = OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as scoped:
        _skip_durability(scoped, module)
        _install_fault(scoped, module, stage_name, exc)
        with pytest.raises(module.AtomicWriteError) as excinfo:
            module.atomic_write_json(target, {"new": True})

    error = excinfo.value
    assert error.stage is module.AtomicWriteStage(stage_name)
    assert error.path == target
    assert error.target_replaced is False
    assert error.__cause__ is exc
    assert str(exc) in str(error), "the cause belongs in the message"
    _assert_preserved(target, previous, listing)


def test_naive_writer_violates_preservation(tmp_path: Path) -> None:
    """The preservation checker detects a truncating writer."""
    target = tmp_path / "naive.json"
    target.write_bytes(_PREVIOUS_DOCUMENT)
    listing = _listing(tmp_path)

    target.write_bytes(b'{"prev')
    with pytest.raises(AssertionError):
        _assert_preserved(target, _PREVIOUS_DOCUMENT, listing)


@pytest.mark.skipif(
    sys.platform == "win32", reason="directory synchronisation is skipped"
)
def test_directory_sync_failure_reports_replaced_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-replace durability failure reports the replaced target and no rollback."""
    module = _atomic_module()
    target = tmp_path / "fixture.json"
    target.write_bytes(_PREVIOUS_DOCUMENT)
    exc = OSError(errno.EIO, "Input/output error")

    def refuse_directory_sync(directory: Path) -> None:
        """Report a genuine I/O error from the directory sync."""
        raise exc

    monkeypatch.setattr(module, "_fsync_directory", refuse_directory_sync)

    with pytest.raises(module.AtomicWriteError) as excinfo:
        module.atomic_write_json(target, {"new": True})

    error = excinfo.value
    assert error.stage is module.AtomicWriteStage.SYNC_DIRECTORY
    assert error.target_replaced is True
    assert error.__cause__ is exc
    assert json.loads(target.read_text(encoding="utf-8")) == {"new": True}
    assert _listing(tmp_path) == {target.name}


def test_keyboard_interrupt_cleans_up_and_propagates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Ctrl-C during the write removes the temporary file and propagates."""
    module = _atomic_module()
    target = tmp_path / "fixture.json"
    target.write_bytes(_PREVIOUS_DOCUMENT)
    listing = _listing(tmp_path)

    with monkeypatch.context() as scoped:
        _skip_durability(scoped, module)
        _install_fault(scoped, module, "write", KeyboardInterrupt())
        with pytest.raises(KeyboardInterrupt):
            module.atomic_write_json(target, {"new": True})

    _assert_preserved(target, _PREVIOUS_DOCUMENT, listing)


def test_cleanup_failure_is_noted_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing clean-up is attached as a note, never raised over the original."""
    module = _atomic_module()
    target = tmp_path / "fixture.json"
    primary = OSError(errno.ENOSPC, "No space left on device")
    cleanup = PermissionError(errno.EACCES, "Permission denied")

    def refuse_removal(temporary: Path) -> None:
        """Refuse to remove the temporary file."""
        raise cleanup

    with monkeypatch.context() as scoped:
        _skip_durability(scoped, module)
        _install_fault(scoped, module, "write", primary)
        scoped.setattr(module, "_discard_temporary", refuse_removal)
        with pytest.raises(module.AtomicWriteError) as excinfo:
            module.atomic_write_json(target, {"new": True})

    error = excinfo.value
    assert error.__cause__ is primary
    notes = getattr(error, "__notes__", [])
    assert any(str(tmp_path) in note for note in notes), notes


def test_descriptor_check_detects_an_open_descriptor(tmp_path: Path) -> None:
    """The descriptor check notices a descriptor the helper could leak."""
    descriptor_path = tmp_path / "held-open"
    fd = os.open(descriptor_path, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        assert _descriptors_under(tmp_path) == {str(descriptor_path)}
    finally:
        os.close(fd)


def test_keyboard_interrupt_notes_a_failed_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An interrupt with a failing clean-up still propagates, with a note."""
    module = _atomic_module()
    target = tmp_path / "fixture.json"
    cleanup = PermissionError(errno.EACCES, "Permission denied")

    def refuse_removal(temporary: Path) -> None:
        """Refuse to remove the temporary file."""
        raise cleanup

    with monkeypatch.context() as scoped:
        _skip_durability(scoped, module)
        _install_fault(scoped, module, "write", KeyboardInterrupt())
        scoped.setattr(module, "_discard_temporary", refuse_removal)
        with pytest.raises(KeyboardInterrupt) as excinfo:
            module.atomic_write_json(target, {"new": True})

    assert excinfo.value.__cause__ is None
    notes = getattr(excinfo.value, "__notes__", [])
    assert any(str(tmp_path) in note for note in notes), notes


@pytest.mark.parametrize("stage_name", tuple(_FAULT_SPECS))
def test_failures_do_not_leak_descriptors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage_name: str
) -> None:
    """No handled failure leaves a descriptor open in the target directory."""
    module = _atomic_module()
    target = tmp_path / "fixture.json"
    before = _descriptors_under(tmp_path)
    exc = OSError(errno.ENOSPC, "No space left on device")

    with monkeypatch.context() as scoped:
        _skip_durability(scoped, module)
        _install_fault(scoped, module, stage_name, exc)
        with pytest.raises(module.AtomicWriteError):
            module.atomic_write_json(target, {"new": True})

    assert _descriptors_under(tmp_path) == before


@pytest.mark.parametrize("first", [None, *_FAULT_SPECS])
@pytest.mark.parametrize("second", [None, *_FAULT_SPECS])
def test_fault_pairs_compose(
    tmp_path: Path, first: str | None, second: str | None
) -> None:
    """Every ordered pair of writes leaves the last committed document."""
    module = _atomic_module()
    target = tmp_path / "fixture.json"
    initial = {"generation": 0}
    target.write_bytes(module._serialize_json(initial))
    run = _PairRun(module=module, target=target, committed=initial)

    with pytest.MonkeyPatch.context() as monkeypatch:
        _skip_durability(monkeypatch, module)
        for index, stage_name in enumerate((first, second)):
            run.apply({"generation": index + 1}, stage_name)

    assert json.loads(target.read_text(encoding="utf-8")) == run.committed
    assert _listing(tmp_path) == {target.name}


def test_interleaved_writers_use_distinct_temporaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two writers never share a temporary file, so neither can tear the other."""
    module = _atomic_module()
    target = tmp_path / "shared.json"
    real_create = module._create_temporary
    real_close = module._close_temporary
    names: list[Path] = []
    failures: list[BaseException] = []
    paused = threading.Event()
    resume = threading.Event()

    def recording_create(path: Path) -> tuple[int, Path]:
        """Record each temporary path before handing it back."""
        fd, temporary = real_create(path)
        names.append(temporary)
        return fd, temporary

    def gated_close(fd: int) -> None:
        """Pause writer A just before it closes its temporary file."""
        if threading.current_thread().name == "writer-a":
            paused.set()
            resume.wait(timeout=10)
        real_close(fd)

    def writer_a() -> None:
        """Write through the paused seam as writer A."""
        try:
            module.atomic_write_json(target, {"writer": "a"})
        except BaseException as exc:  # ruff: ignore[blind-except] - re-raised in the main thread below
            failures.append(exc)

    monkeypatch.setattr(module, "_create_temporary", recording_create)
    monkeypatch.setattr(module, "_close_temporary", gated_close)
    thread = threading.Thread(target=writer_a, name="writer-a")
    thread.start()
    try:
        assert paused.wait(timeout=10), "writer A never reached the close stage"
        module.atomic_write_json(target, {"writer": "b"})
    finally:
        resume.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert not failures, failures
    assert json.loads(target.read_text(encoding="utf-8")) == {"writer": "a"}
    assert len(names) == 2
    assert names[0] != names[1]
    assert _listing(tmp_path) == {target.name}
