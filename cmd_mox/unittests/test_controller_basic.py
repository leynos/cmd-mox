"""Unit tests for :mod:`cmd_mox.controller` - basic functionality."""

from __future__ import annotations

import os
import typing as t

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.test_doubles import MockCommand, SpyCommand, StubCommand
from cmd_mox.unittests._env_helpers import require_shim_dir

pytestmark = pytest.mark.requires_unix_sockets

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    import subprocess


def test_cmdmox_stub_records_invocation(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Stubbed command returns configured output and journal records call."""
    original_path = os.environ["PATH"]
    mox = CmdMox()
    mox.stub("hello").returns(stdout="hi")
    mox.__enter__()
    mox.replay()

    cmd_path = require_shim_dir(mox.environment) / "hello"
    result = run([str(cmd_path)])
    mox.verify()

    assert result.stdout.strip() == "hi"
    assert len(mox.journal) == 1
    assert mox.journal[0].command == "hello"
    assert os.environ["PATH"] == original_path


def test_factory_methods_create_distinct_objects() -> None:
    """CmdMox exposes mock() and spy() alongside stub()."""
    mox = CmdMox()
    assert isinstance(mox.stub("a"), StubCommand)
    assert isinstance(mox.mock("b"), MockCommand)
    assert isinstance(mox.spy("c"), SpyCommand)


def test_mock_idempotency() -> None:
    """Repeated calls to mock() with the same name return the same object."""
    mox = CmdMox()
    m1 = mox.mock("foo")
    m2 = mox.mock("foo")
    assert m1 is m2


def test_stub_idempotency() -> None:
    """Repeated calls to stub() with the same name return the same object."""
    mox = CmdMox()
    s1 = mox.stub("bar")
    s2 = mox.stub("bar")
    assert s1 is s2


def test_spy_idempotency() -> None:
    """Repeated calls to spy() with the same name return the same object."""
    mox = CmdMox()
    s1 = mox.spy("bar")
    s2 = mox.spy("bar")
    assert s1 is s2


def test_double_kind_mismatch() -> None:
    """Requesting a different kind for an existing double raises ``ValueError``."""
    mox = CmdMox()
    mox.stub("foo")
    with pytest.raises(ValueError, match="registered as stub"):
        mox.mock("foo")


def test_mock_and_spy_invocations(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Mock and spy commands record calls and verify correctly."""
    mox = CmdMox()
    mox.mock("hello").returns(stdout="hi")
    mox.spy("world").returns(stdout="earth")
    mox.__enter__()
    mox.replay()

    shim_dir = require_shim_dir(mox.environment)
    cmd_hello = shim_dir / "hello"
    cmd_world = shim_dir / "world"
    res1 = run([str(cmd_hello)])
    res2 = run([str(cmd_world)])

    mox.verify()

    assert res1.stdout.strip() == "hi"
    assert res2.stdout.strip() == "earth"
    assert len(mox.journal) == 2
    assert mox.mocks["hello"].invocations[0].command == "hello"
    assert mox.spies["world"].invocations[0].command == "world"


def test_invocation_order_multiple_calls(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Multiple calls are recorded in order."""
    mox = CmdMox()
    mox.mock("hello").returns(stdout="hi").times(2)
    mox.spy("world").returns(stdout="earth")
    mox.__enter__()
    mox.replay()

    shim_dir = require_shim_dir(mox.environment)
    cmd_hello = shim_dir / "hello"
    cmd_world = shim_dir / "world"
    run([str(cmd_hello)])
    run([str(cmd_world)])
    run([str(cmd_hello)])

    mox.verify()

    assert [inv.command for inv in mox.journal] == ["hello", "world", "hello"]
    assert len(mox.mocks["hello"].invocations) == 2
    assert len(mox.spies["world"].invocations) == 1


def test_is_recording_property() -> None:
    """is_recording is True for mocks and spies, False for stubs."""
    mox = CmdMox()
    stub = mox.stub("a")
    mock = mox.mock("b")
    spy = mox.spy("c")

    assert not stub.is_recording
    assert mock.is_recording
    assert spy.is_recording
