"""Tests covering verification error reporting."""

from __future__ import annotations

import typing as t
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.errors import UnexpectedCommandError, UnfulfilledExpectationError

pytestmark = pytest.mark.requires_unix_sockets

if t.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import subprocess


def test_unexpected_invocation_message_includes_diff(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Mismatched arguments surface in the verification error message."""
    mox = CmdMox()
    mox.mock("git").with_args("status")
    mox.__enter__()
    mox.replay()

    assert mox.environment.shim_dir is not None
    path = Path(mox.environment.shim_dir) / "git"
    run([str(path), "commit"], shell=False)

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox.verify()

    message = str(excinfo.value)
    assert "Unexpected command invocation." in message
    assert "git('status')" in message
    assert "git('commit')" in message


def test_unfulfilled_expectation_message_includes_counts(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Unfulfilled expectations report expected and observed call counts."""
    mox = CmdMox()
    mox.mock("sync").returns(stdout="ok").times(2)
    mox.__enter__()
    mox.replay()

    assert mox.environment.shim_dir is not None
    path = Path(mox.environment.shim_dir) / "sync"
    run([str(path)], shell=False)

    with pytest.raises(UnfulfilledExpectationError) as excinfo:
        mox.verify()

    message = str(excinfo.value)
    assert "Unfulfilled expectation." in message
    assert "expected calls=2" in message
    assert "1 (expected 2)" in message


def test_order_violation_reports_first_mismatch(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Ordered expectations report the first mismatching position."""
    mox = CmdMox()
    mox.mock("first").with_args("a").returns(stdout="1").in_order()
    mox.mock("second").with_args("b").returns(stdout="2").in_order()
    mox.__enter__()
    mox.replay()

    assert mox.environment.shim_dir is not None
    shim_first = Path(mox.environment.shim_dir) / "first"
    shim_second = Path(mox.environment.shim_dir) / "second"
    run([str(shim_second), "b"], shell=False)
    run([str(shim_first), "a"], shell=False)

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox.verify()

    message = str(excinfo.value)
    assert "Ordered expectation violated." in message
    assert "position 1" in message
    assert "first" in message
    assert "second" in message


def test_extra_invocation_reports_count(
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Extra invocations report the observed call count."""
    mox = CmdMox()
    mox.mock("echo").returns(stdout="ok").times(1)
    mox.__enter__()
    mox.replay()

    assert mox.environment.shim_dir is not None
    path = Path(mox.environment.shim_dir) / "echo"
    run([str(path)], shell=False)
    run([str(path)], shell=False)

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox.verify()

    message = str(excinfo.value)
    assert "Unexpected additional invocation." in message
    assert "Observed calls" in message
    assert "Last call" in message
