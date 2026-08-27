"""Tests covering verification error reporting."""

from __future__ import annotations

import typing as typ

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.errors import UnexpectedCommandError, UnfulfilledExpectationError
from cmd_mox.unittests._env_helpers import require_shim_dir

pytestmark = pytest.mark.requires_unix_sockets

if typ.TYPE_CHECKING:  # pragma: no cover - used only for typing
    import collections.abc as cabc
    import subprocess


def _assert_message_contains(
    excinfo: pytest.ExceptionInfo[BaseException], *fragments: str
) -> None:
    """Assert the captured exception message contains every fragment.

    Parameters
    ----------
    excinfo : pytest.ExceptionInfo
        Exception information captured by ``pytest.raises``.
    *fragments : str
        Substrings that must all appear in the rendered message.
    """
    message = str(excinfo.value)
    for fragment in fragments:
        assert fragment in message, f"{fragment!r} missing from {message!r}"


def test_unexpected_invocation_message_includes_diff(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Mismatched arguments surface in the verification error message."""
    mox = CmdMox()
    mox.mock("git").with_args("status")
    mox.__enter__()
    mox.replay()

    path = require_shim_dir(mox.environment) / "git"
    run([str(path), "commit"], shell=False)

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox.verify()

    _assert_message_contains(
        excinfo,
        "Unexpected command invocation.",
        "git('status')",
        "git('commit')",
    )


def test_unfulfilled_expectation_message_includes_counts(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Unfulfilled expectations report expected and observed call counts."""
    mox = CmdMox()
    mox.mock("sync").returns(stdout="ok").times(2)
    mox.__enter__()
    mox.replay()

    path = require_shim_dir(mox.environment) / "sync"
    run([str(path)], shell=False)

    with pytest.raises(UnfulfilledExpectationError) as excinfo:
        mox.verify()

    _assert_message_contains(
        excinfo,
        "Unfulfilled expectation.",
        "expected calls=2",
        "1 (expected 2)",
    )


def test_order_violation_reports_first_mismatch(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Ordered expectations report the first mismatching position."""
    mox = CmdMox()
    mox.mock("first").with_args("a").returns(stdout="1").in_order()
    mox.mock("second").with_args("b").returns(stdout="2").in_order()
    mox.__enter__()
    mox.replay()

    shim_dir = require_shim_dir(mox.environment)
    shim_first = shim_dir / "first"
    shim_second = shim_dir / "second"
    run([str(shim_second), "b"], shell=False)
    run([str(shim_first), "a"], shell=False)

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox.verify()

    _assert_message_contains(
        excinfo,
        "Ordered expectation violated.",
        "position 1",
        "first",
        "second",
    )


def test_extra_invocation_reports_count(
    run: cabc.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Extra invocations report the observed call count."""
    mox = CmdMox()
    mox.mock("echo").returns(stdout="ok").times(1)
    mox.__enter__()
    mox.replay()

    path = require_shim_dir(mox.environment) / "echo"
    run([str(path)], shell=False)
    run([str(path)], shell=False)

    with pytest.raises(UnexpectedCommandError) as excinfo:
        mox.verify()

    _assert_message_contains(
        excinfo,
        "Unexpected additional invocation.",
        "Observed calls",
        "Last call",
    )
