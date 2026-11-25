"""Unit tests for the internal atomic write helpers."""

from __future__ import annotations

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
        target = Path(directory) / "nested" / "payload.json"
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
    assert "write" in str(error)
    assert "intact" in str(error)

    replaced = module.AtomicWriteError(
        target, module.AtomicWriteStage.SYNC_DIRECTORY, cause
    )
    assert replaced.target_replaced is True
    assert "replaced" in str(replaced)

    caller_bug = module.AtomicWriteError(
        target, module.AtomicWriteStage.SERIALIZE, TypeError("not JSON")
    )
    assert caller_bug.errno is None


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

    untouched = tmp_path / "missing" / "payload.json"
    with pytest.raises(ValueError, match="mode"):
        module.atomic_write_json(untouched, {"a": 1}, mode=0o444)
    assert not untouched.parent.exists(), "mode validation must precede mutation"


@pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission bits are not enforced"
)
@pytest.mark.parametrize("umask_value", _UMASK_VALUES)
@pytest.mark.parametrize("mode", _REQUESTED_MODES)
@pytest.mark.parametrize("prior_mode", _PRIOR_MODES)
def test_mode_is_exact_regardless_of_umask(
    tmp_path: Path,
    set_umask: cabc.Callable[[int], None],
    umask_value: int,
    mode: int,
    prior_mode: int | None,
) -> None:
    """The final mode equals the requested mode whatever the umask."""
    module = _atomic_module()
    target = tmp_path / "mode.json"
    if prior_mode is not None:
        target.write_bytes(b'{"previous": true}\n')
        target.chmod(prior_mode)

    set_umask(umask_value)
    module.atomic_write_json(target, {"mode": mode}, mode=mode)

    assert stat.S_IMODE(target.stat().st_mode) == mode
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
    caplog.set_level(logging.DEBUG, logger="cmd_mox._atomic_write")
    try:
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
    assert len(messages) == 1
    assert str(tmp_path) in messages[0]


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


def test_windows_retries_replace_permission_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient sharing violation on Windows is retried before failing."""
    module = _atomic_module()
    from cmd_mox import _path_utils

    attempts: list[int] = []
    real_replace = module._os_replace

    def flaky_replace(source: Path, destination: Path) -> None:
        """Fail the first two replacements, then delegate to the real one."""
        attempts.append(1)
        if len(attempts) < 3:
            raise PermissionError(errno.EACCES, "sharing violation")
        real_replace(source, destination)

    monkeypatch.setattr(_path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(module, "_os_replace", flaky_replace)
    target = tmp_path / "payload.json"

    module.atomic_write_json(target, {"ok": True})

    assert len(attempts) == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": True}


def test_windows_replace_retry_exhausts_and_reports_replace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persistent sharing violation stops after the retry budget."""
    module = _atomic_module()
    from cmd_mox import _path_utils

    attempts: list[int] = []

    def refuse_replace(source: Path, destination: Path) -> None:
        """Report a sharing violation unconditionally."""
        attempts.append(1)
        raise PermissionError(errno.EACCES, "sharing violation")

    monkeypatch.setattr(_path_utils, "IS_WINDOWS", True)
    monkeypatch.setattr(module, "_os_replace", refuse_replace)
    target = tmp_path / "payload.json"

    with pytest.raises(module.AtomicWriteError) as excinfo:
        module.atomic_write_json(target, {"ok": True})

    assert excinfo.value.stage is module.AtomicWriteStage.REPLACE
    assert len(attempts) == 5


def test_posix_does_not_retry_replace_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSIX does not retry, because it has no sharing violations."""
    module = _atomic_module()
    from cmd_mox import _path_utils

    attempts: list[int] = []

    def refuse_replace(source: Path, destination: Path) -> None:
        """Report an unavailable target."""
        attempts.append(1)
        raise PermissionError(errno.EACCES, "denied")

    monkeypatch.setattr(_path_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(module, "_os_replace", refuse_replace)
    target = tmp_path / "payload.json"

    with pytest.raises(module.AtomicWriteError):
        module.atomic_write_json(target, {"ok": True})

    assert len(attempts) == 1
