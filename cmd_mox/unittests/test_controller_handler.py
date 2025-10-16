"""Unit tests for :mod:`cmd_mox.controller` - handler invocation."""

from __future__ import annotations

import typing as t
from pathlib import Path

import pytest

from cmd_mox.controller import CmdMox
from cmd_mox.ipc import Invocation, Response

pytestmark = pytest.mark.requires_unix_sockets

if t.TYPE_CHECKING:  # pragma: no cover - typing only
    import subprocess


def _tuple_handler(invocation: Invocation) -> tuple[str, str, int]:
    assert invocation.args == []
    return ("handled", "", 0)


def _response_handler(invocation: Invocation) -> Response:
    assert invocation.args == []
    return Response(stdout="r", stderr="", exit_code=0)


@pytest.mark.parametrize(
    ("cmd", "handler", "expected"),
    [
        ("dyn", _tuple_handler, "handled"),
        ("obj", _response_handler, "r"),
    ],
)
def test_stub_runs_handler(
    cmd: str,
    handler: t.Callable[[Invocation], Response | tuple[str, str, int]],
    expected: str,
    run: t.Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    """Stub runs a dynamic handler when invoked."""
    mox = CmdMox()
    mox.stub(cmd).runs(handler)
    mox.__enter__()
    mox.replay()

    cmd_path = Path(mox.environment.shim_dir) / cmd
    result = run([str(cmd_path)])

    mox.verify()

    assert result.stdout.strip() == expected
