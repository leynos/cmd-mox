"""Unit tests for the internal atomic write helpers."""

from __future__ import annotations

import dataclasses as dc
import errno
import importlib
import json
import logging
import os
import stat
import sys
import tempfile
import typing as typ
from pathlib import Path

import hypothesis
import hypothesis.strategies as st
import pytest
from hypothesis import example, given, settings

if typ.TYPE_CHECKING:
    import collections.abc as cabc
    import types

    from syrupy.assertion import SnapshotAssertion

#: Lone surrogates reach fixtures when arguments are decoded with
#: ``surrogateescape``, so the round-trip domain must include them.
_SURROGATE_TEXT = st.text(
    alphabet=st.characters(min_codepoint=0xDC80, max_codepoint=0xDCFF)
)
_JSON_TEXT = st.one_of(st.text(), _SURROGATE_TEXT)
_JSON_SCALARS = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(allow_nan=False, allow_infinity=False),
    _JSON_TEXT,
)
_JSON_VALUES = st.recursive(
    _JSON_SCALARS,
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(_JSON_TEXT, children, max_size=4),
    ),
    max_leaves=8,
)

_SNAPSHOT_PAYLOADS = [
    pytest.param(
        {"zebra": 1, "alpha": [1, 2], "middle": {"beta": None}},
        id="unsorted-mapping",
    ),
    pytest.param({"text": "héllo 😀", "surrogate": "\udcff"}, id="unicode-text"),
    pytest.param("top-level scalar", id="top-level-scalar"),
]

_UMASK_VALUES = (0o000, 0o022, 0o077)
_REQUESTED_MODES = (0o600, 0o640, 0o644, 0o755)
_PRIOR_MODES = (None, 0o644, 0o666)


def _can_open_directory(directory: Path) -> bool:
    """Report whether this process can read *directory* without permission."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(directory, flags)
    except PermissionError:
        return False
    os.close(descriptor)
    return True


def _atomic_module() -> types.ModuleType:
    """Return the internal atomic write module under test."""
    return importlib.import_module("cmd_mox._atomic_write")


def _has_surrogate(text: str) -> bool:
    """Report whether *text* contains a surrogate code point."""
    return any(0xD800 <= ord(char) <= 0xDFFF for char in text)


def _combine(features: cabc.Iterable[tuple[bool, bool]]) -> tuple[bool, bool]:
    """Combine the text feature flags of several values."""
    flags = list(features)
    return (
        any(has_non_ascii for has_non_ascii, _ in flags),
        any(has_surrogate for _, has_surrogate in flags),
    )


def _text_features(value: object) -> tuple[bool, bool]:
    """Report whether *value* holds non-ASCII text or a lone surrogate."""
    if isinstance(value, str):
        return (not value.isascii(), _has_surrogate(value))
    if isinstance(value, list):
        return _combine(_text_features(item) for item in value)
    if isinstance(value, dict):
        return _combine(
            [_text_features(key) for key in value]
            + [_text_features(item) for item in value.values()]
        )
    return (False, False)


def _circular_payload() -> list[object]:
    """Return a list that contains itself, which JSON cannot serialize."""
    payload: list[object] = []
    payload.append(payload)
    return payload


@pytest.fixture
def set_umask() -> cabc.Iterator[cabc.Callable[[int], None]]:
    """Set the process umask for one test and restore it afterwards."""

    def apply(value: int) -> None:
        """Set the process umask to *value*."""
        os.umask(value)

    previous = os.umask(0)
    os.umask(previous)
    try:
        yield apply
    finally:
        os.umask(previous)


@given(payload=_JSON_VALUES)
# Named examples guarantee the classification below reports every top-level
# type: Hypothesis's recursive sampler reaches bare scalars only rarely.
@example(payload=None)
@example(payload=True)
@example(payload=7)
@example(payload=1.5)
@example(payload="plain text")
def test_serialization_round_trips(payload: object) -> None:
    """Serialized JSON loads back as an equal payload."""
    module = _atomic_module()

    encoded = module._serialize_json(payload)

    assert json.loads(encoded.decode("utf-8")) == payload
    has_non_ascii, has_surrogate = _text_features(payload)
    hypothesis.event(f"top_level={type(payload).__name__}")
    hypothesis.event(f"non_ascii={has_non_ascii}")
    hypothesis.event(f"surrogate={has_surrogate}")


@pytest.mark.parametrize(
    "payload",
    [None, True, 7, 1.5, "plain text", [1, 2], {"a": 1}],
    ids=["null", "bool", "int", "float", "text", "list", "mapping"],
)
def test_every_top_level_type_round_trips(payload: object) -> None:
    """Every JSON top-level type survives the serializer."""
    module = _atomic_module()

    assert json.loads(module._serialize_json(payload).decode("utf-8")) == payload


@given(payload=_JSON_VALUES)
@settings(max_examples=25, deadline=None)
def test_written_file_round_trips(payload: object) -> None:
    """A written file holds exactly the serialized payload."""
    module = _atomic_module()
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "nested" / "deeper" / "payload.json"
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.setattr(module, "_fsync_descriptor", lambda _fd: None)
            module.atomic_write_json(target, payload)

        assert target.read_bytes() == module._serialize_json(payload)
        assert json.loads(target.read_text(encoding="utf-8")) == payload


@pytest.mark.parametrize("payload", _SNAPSHOT_PAYLOADS)
def test_serialized_bytes_snapshot(
    payload: object, snapshot: SnapshotAssertion
) -> None:
    """Pin the exact bytes fixtures and state files are written with."""
    module = _atomic_module()

    assert module._serialize_json(payload).decode("utf-8") == snapshot


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({1, 2}, id="set"),
        pytest.param(_circular_payload(), id="circular"),
        pytest.param(float("nan"), id="nan"),
    ],
)
def test_unserializable_payload_touches_nothing(
    tmp_path: Path, payload: object
) -> None:
    """An unserializable payload fails before any filesystem work."""
    module = _atomic_module()
    target = tmp_path / "missing" / "payload.json"

    with pytest.raises(module.AtomicWriteError) as excinfo:
        module.atomic_write_json(target, payload)

    error = excinfo.value
    assert error.stage is module.AtomicWriteStage.SERIALIZE
    assert error.target_replaced is False
    assert isinstance(error.__cause__, (TypeError, ValueError))
    assert str(error.__cause__) in str(error), "the cause belongs in the message"
    assert not target.parent.exists(), "serialization must precede mutation"


def test_error_preserves_cause_errno(tmp_path: Path) -> None:
    """The wrapper keeps the cause errno and names the target."""
    module = _atomic_module()
    target = tmp_path / "payload.json"
    cause = OSError(errno.ENOSPC, "No space left on device")

    error = module.AtomicWriteError(target, module.AtomicWriteStage.WRITE, cause)

    assert isinstance(error, OSError)
    assert error.errno == errno.ENOSPC
    assert error.filename == str(target)
    assert error.path == target
    assert error.target_replaced is False
    assert error.strerror is not None
    assert "write" in error.strerror
    assert error.strerror.endswith("the previous file was left intact")
    assert "No space left on device" in error.strerror

    replaced = module.AtomicWriteError(
        target, module.AtomicWriteStage.SYNC_DIRECTORY, cause
    )
    assert replaced.target_replaced is True
    assert replaced.strerror is not None
    assert replaced.strerror.endswith("the target was replaced")

    caller_bug = module.AtomicWriteError(
        target, module.AtomicWriteStage.SERIALIZE, TypeError("not JSON")
    )
    assert caller_bug.errno is None
    assert "not JSON" in str(caller_bug)
    assert str(caller_bug).endswith("the previous file was left intact")


def test_mode_validation_is_exact(tmp_path: Path) -> None:
    """The validator accepts exactly the modes that grant owner read and write."""
    module = _atomic_module()
    accepted = [mode for mode in range(0o1000) if mode & 0o600 == 0o600]
    rejected = [mode for mode in range(0o1000) if mode & 0o600 != 0o600]

    assert len(accepted) == 128
    assert len(rejected) == 384
    for mode in accepted:
        module._validate_mode(mode)
    for mode in rejected:
        with pytest.raises(ValueError, match="mode"):
            module._validate_mode(mode)
    for invalid in (-1, 0o1000, True, 0.0, "600"):
        with pytest.raises(ValueError, match="mode"):
            module._validate_mode(invalid)
    with pytest.raises(ValueError, match="got float"):
        module._validate_mode(0.0)
    with pytest.raises(ValueError, match="got str"):
        module._validate_mode("600")

    untouched = tmp_path / "missing" / "payload.json"
    with pytest.raises(ValueError, match="mode"):
        module.atomic_write_json(untouched, {"a": 1}, mode=0o444)
    assert not untouched.parent.exists(), "mode validation must precede mutation"


@dc.dataclass(frozen=True, slots=True)
class _ModeCase:
    """One umask, requested mode, and prior mode combination."""

    umask_value: int
    mode: int
    prior_mode: int | None


_MODE_CASES = [
    pytest.param(
        _ModeCase(umask_value, mode, prior_mode),
        id=f"{umask_value:03o}-{mode:03o}-{prior_mode}",
    )
    for umask_value in _UMASK_VALUES
    for mode in _REQUESTED_MODES
    for prior_mode in _PRIOR_MODES
]


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not enforced"
)
@pytest.mark.parametrize("case", _MODE_CASES)
def test_mode_is_exact_regardless_of_umask(
    tmp_path: Path, set_umask: cabc.Callable[[int], None], case: _ModeCase
) -> None:
    """The final mode equals the requested mode whatever the umask."""
    module = _atomic_module()
    target = tmp_path / "mode.json"
    if case.prior_mode is not None:
        target.write_bytes(b'{"previous": true}\n')
        target.chmod(case.prior_mode)

    set_umask(case.umask_value)
    module.atomic_write_json(target, {"mode": case.mode}, mode=case.mode)

    assert stat.S_IMODE(target.stat().st_mode) == case.mode
    assert 0o077 in _UMASK_VALUES
    assert any(candidate != 0o600 for candidate in _REQUESTED_MODES)


def test_temporary_prefix_truncates_by_bytes() -> None:
    """The temporary prefix stays within 66 bytes without splitting a character."""
    module = _atomic_module()

    prefix = module._temporary_prefix("😀" * 62)

    assert prefix == "." + "😀" * 16 + "."
    assert len(prefix.encode("utf-8")) == 66


def test_long_multibyte_names_are_supported(tmp_path: Path) -> None:
    """A 253-byte multibyte name still fits beside its temporary file."""
    module = _atomic_module()
    name = "😀" * 62 + "abcde"
    assert len(os.fsencode(name)) == 253
    target = tmp_path / name

    module.atomic_write_json(target, {"ok": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_temporary_prefix_drops_a_split_character() -> None:
    """Truncation never leaves a partial multibyte character in the prefix."""
    module = _atomic_module()

    prefix = module._temporary_prefix("\u2603" * 40)

    assert prefix == "." + "\u2603" * 21 + "."
    assert len(prefix.encode("utf-8")) == 65


def test_temporary_prefix_handles_surrogate_names() -> None:
    """A name holding a lone surrogate still yields an encodable prefix."""
    module = _atomic_module()

    prefix = module._temporary_prefix("\udcff" + "a" * 70)

    assert prefix.startswith(".\udcff")
    assert len(prefix.encode("utf-8", errors="surrogatepass")) <= 66


def test_temporary_file_matches_the_documented_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sibling temporary file is hidden, prefixed, and .tmp suffixed."""
    module = _atomic_module()
    created: list[Path] = []
    real_create = module._create_temporary

    def recording_create(path: Path) -> tuple[int, Path]:
        """Record each temporary path before handing it back."""
        fd, temporary = real_create(path)
        created.append(temporary)
        return fd, temporary

    monkeypatch.setattr(module, "_create_temporary", recording_create)

    module.atomic_write_json(tmp_path / "fixture.json", {"a": 1})

    assert len(created) == 1
    name = created[0].name
    assert created[0].parent == tmp_path
    assert name.startswith(".fixture.json.")
    assert name.endswith(".tmp")


def test_write_all_completes_short_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A short write is resumed until every byte reaches the file."""
    module = _atomic_module()
    target = tmp_path / "payload.bin"
    real_write = module._write_once
    payload = b"abcdefghij"
    chunks: list[int] = []

    def short_write(fd: int, buffer: memoryview) -> int:
        """Accept at most four bytes per call, as a signal-interrupted write may."""
        chunk = bytes(buffer[:4])
        chunks.append(len(chunk))
        return real_write(fd, memoryview(chunk))

    monkeypatch.setattr(module, "_write_once", short_write)

    module.atomic_write_bytes(target, payload)

    assert target.read_bytes() == payload
    assert chunks == [4, 4, 2]


def test_temporary_file_for_a_long_name_keeps_the_tmp_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A truncated prefix still leaves a dot-prefixed .tmp orphan pattern."""
    module = _atomic_module()
    created: list[Path] = []
    real_create = module._create_temporary
    name = "a" * 80 + ".json"

    def recording_create(path: Path) -> tuple[int, Path]:
        """Record each temporary path before handing it back."""
        fd, temporary = real_create(path)
        created.append(temporary)
        return fd, temporary

    monkeypatch.setattr(module, "_create_temporary", recording_create)

    module.atomic_write_json(tmp_path / name, {"a": 1})

    assert len(created) == 1
    temporary_name = created[0].name
    assert temporary_name.startswith("." + "a" * 64 + ".")
    assert temporary_name.endswith(".tmp")
    assert ".json" not in temporary_name, "the suffix is truncated away"


@pytest.mark.skipif(
    sys.platform == "win32", reason="directory synchronisation is skipped"
)
def test_directory_close_error_does_not_mask_sync_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing directory close keeps the original synchronization error."""
    module = _atomic_module()
    target = tmp_path / "payload.json"
    sync_error = OSError(errno.EIO, "Input/output error")
    close_error = OSError(errno.EIO, "close failed")

    def fail_directory_sync(fd: int) -> None:
        """Fail only for the directory descriptor."""
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise sync_error
        os.fsync(fd)

    real_close = module._close_descriptor

    def fail_directory_close(fd: int) -> None:
        """Report a close failure only for the directory descriptor."""
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise close_error
        real_close(fd)

    monkeypatch.setattr(module, "_fsync_descriptor", fail_directory_sync)
    monkeypatch.setattr(module, "_close_descriptor", fail_directory_close)

    with pytest.raises(module.AtomicWriteError) as excinfo:
        module.atomic_write_json(target, {"ok": True})

    assert excinfo.value.stage is module.AtomicWriteStage.SYNC_DIRECTORY
    assert excinfo.value.__cause__ is sync_error


def test_written_bytes_never_translate_newlines(tmp_path: Path) -> None:
    """The binary write never applies platform newline translation."""
    module = _atomic_module()
    target = tmp_path / "payload.json"

    module.atomic_write_json(target, {"a": 1})

    data = target.read_bytes()
    assert b"\r" not in data
    assert data.endswith(b"\n")


def test_cleanup_notes_a_failed_close() -> None:
    """A descriptor that cannot be closed becomes a note, not an exception."""
    module = _atomic_module()

    notes = module._cleanup_after_failure(-1, None)

    assert len(notes) == 1
    assert "Failed to close the temporary file" in notes[0]


def test_discard_temporary_tolerates_a_missing_file(tmp_path: Path) -> None:
    """Cleanup does not fail when the temporary file already vanished."""
    module = _atomic_module()

    module._discard_temporary(tmp_path / "already-gone.tmp")

    assert not (tmp_path / "already-gone.tmp").exists()


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not enforced"
)
def test_atomic_write_bytes_defaults_to_owner_only(tmp_path: Path) -> None:
    """The bytes helper defaults to mode 0o600 like the JSON helper."""
    module = _atomic_module()
    target = tmp_path / "payload.bin"

    module.atomic_write_bytes(target, b"data")

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("raw", ["", "/", "."])
def test_empty_names_are_rejected(raw: str) -> None:
    """A target that names no file is rejected before filesystem work."""
    module = _atomic_module()

    with pytest.raises(ValueError, match="target path"):
        module.atomic_write_json(Path(raw), {"a": 1})


def test_directory_target_fails_at_replace(tmp_path: Path) -> None:
    """Replacing a directory fails at the replace stage and leaves no residue."""
    module = _atomic_module()
    target = tmp_path / "directory_target"
    target.mkdir()
    listing = {entry.name for entry in tmp_path.iterdir()}

    with pytest.raises(module.AtomicWriteError) as excinfo:
        module.atomic_write_json(target, {"a": 1})

    assert excinfo.value.stage is module.AtomicWriteStage.REPLACE
    assert excinfo.value.target_replaced is False
    assert target.is_dir()
    assert {entry.name for entry in tmp_path.iterdir()} == listing


@pytest.mark.skipif(
    sys.platform == "win32", reason="symlink creation needs extra privileges"
)
def test_symlink_target_is_replaced_not_followed(tmp_path: Path) -> None:
    """A symlink target becomes a regular file and its destination is untouched."""
    module = _atomic_module()
    destination = tmp_path / "destination.json"
    destination.write_bytes(b'{"old": true}\n')
    link = tmp_path / "link.json"
    link.symlink_to(destination)

    module.atomic_write_json(link, {"new": True})

    assert not link.is_symlink()
    assert json.loads(link.read_text(encoding="utf-8")) == {"new": True}
    assert destination.read_bytes() == b'{"old": true}\n'


@pytest.mark.skipif(
    sys.platform == "win32", reason="directory synchronisation is skipped"
)
def test_unsupported_directory_sync_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A parent directory that cannot be read is an explicit, logged skip."""
    module = _atomic_module()
    directory = tmp_path / "locked"
    directory.mkdir()
    directory.chmod(0o300)
    try:
        if _can_open_directory(directory):
            pytest.skip("process bypasses directory read permissions")
        caplog.set_level(logging.DEBUG, logger="cmd_mox._atomic_write")
        module.atomic_write_json(directory / "payload.json", {"ok": True})
    finally:
        directory.chmod(0o700)

    assert (directory / "payload.json").exists()
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "cmd_mox._atomic_write"
    ]
    assert len(messages) == 1
    assert str(directory) in messages[0]


@pytest.mark.skipif(
    sys.platform == "win32", reason="directory synchronisation is skipped"
)
@pytest.mark.parametrize(
    "error_number", sorted({errno.EINVAL, errno.ENOTSUP, errno.EOPNOTSUPP})
)
def test_unsupported_directory_sync_errnos_are_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_number: int,
) -> None:
    """A "not supported here" error from the directory sync is skipped."""
    module = _atomic_module()

    real_fsync = module._fsync_descriptor

    def fail_directory_sync(fd: int) -> None:
        """Raise *error_number* for directory descriptors, otherwise sync."""
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError(error_number, os.strerror(error_number))
        real_fsync(fd)

    monkeypatch.setattr(module, "_fsync_descriptor", fail_directory_sync)
    caplog.set_level(logging.DEBUG, logger="cmd_mox._atomic_write")
    target = tmp_path / "payload.json"

    module.atomic_write_json(target, {"ok": True})

    assert target.exists()
    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "cmd_mox._atomic_write"
    ]
    assert messages == [
        f"Directory synchronization is unsupported for {tmp_path}; skipping"
    ]


def test_windows_skips_directory_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows skips directory synchronisation entirely."""
    module = _atomic_module()
    from cmd_mox import _path_utils

    def forbid_directory_sync(fd: int) -> None:
        """Fail if a directory descriptor is synchronised."""
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            msg = "directory sync must be skipped on Windows"
            raise AssertionError(msg)

    monkeypatch.setattr(_path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(module, "_fsync_descriptor", forbid_directory_sync)
    target = tmp_path / "payload.json"

    module.atomic_write_json(target, {"ok": True})

    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_missing_fchmod_falls_back_to_chmod(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python 3.12 on Windows has no os.fchmod, so the chmod fallback applies."""
    module = _atomic_module()
    monkeypatch.setattr(module, "_FCHMOD", None)
    target = tmp_path / "payload.json"

    module.atomic_write_json(target, {"ok": True}, mode=0o640)

    assert target.exists()
    if sys.platform != "win32":
        assert stat.S_IMODE(target.stat().st_mode) == 0o640


@dc.dataclass(frozen=True, slots=True, kw_only=True)
class _ReplaceCase:
    """One replace-retry scenario."""

    is_windows: bool
    failures: int | None
    expected_attempts: int
    should_succeed: bool


_REPLACE_CASES = [
    pytest.param(
        _ReplaceCase(
            is_windows=True, failures=2, expected_attempts=3, should_succeed=True
        ),
        id="windows-transient",
    ),
    pytest.param(
        _ReplaceCase(
            is_windows=True, failures=4, expected_attempts=5, should_succeed=True
        ),
        id="windows-final-attempt",
    ),
    pytest.param(
        _ReplaceCase(
            is_windows=True, failures=None, expected_attempts=5, should_succeed=False
        ),
        id="windows-exhausted",
    ),
    pytest.param(
        _ReplaceCase(
            is_windows=False, failures=None, expected_attempts=1, should_succeed=False
        ),
        id="posix-no-retry",
    ),
]


@pytest.mark.parametrize("case", _REPLACE_CASES)
def test_replace_retry_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: _ReplaceCase
) -> None:
    """The retry budget, and its outcome, match the platform."""
    module = _atomic_module()
    from cmd_mox import _path_utils

    attempts: list[tuple[Path, Path]] = []
    real_replace = module._os_replace

    def flaky_replace(source: Path, destination: Path) -> None:
        """Fail per the case, then delegate to the real rename."""
        attempts.append((source, destination))
        if case.failures is None or len(attempts) <= case.failures:
            raise PermissionError(errno.EACCES, "sharing violation")
        real_replace(source, destination)

    monkeypatch.setattr(_path_utils, "IS_WINDOWS", case.is_windows)
    monkeypatch.setattr(module, "_os_replace", flaky_replace)
    target = tmp_path / "payload.json"

    if case.should_succeed:
        module.atomic_write_json(target, {"ok": True})
        assert attempts[-1][1] == target
        assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}
    else:
        with pytest.raises(module.AtomicWriteError) as excinfo:
            module.atomic_write_json(target, {"ok": True})
        assert excinfo.value.stage is module.AtomicWriteStage.REPLACE
    assert len(attempts) == case.expected_attempts
