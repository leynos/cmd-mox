"""Unit tests for Windows batch argument escaping helper."""

from __future__ import annotations

import typing as typ

import pytest

from tests.helpers import controller

if typ.TYPE_CHECKING:
    import collections.abc as cabc


@pytest.fixture
def windows_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force helpers to behave as if running on Windows.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to patch the platform indicator consulted by the helper.
    """
    monkeypatch.setattr(controller.os, "name", "nt")


@pytest.fixture
def posix_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force helpers to behave as if running on POSIX.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to patch the platform indicator consulted by the helper.
    """
    monkeypatch.setattr(controller.os, "name", "posix")


@pytest.mark.usefixtures("posix_platform")
def test_escape_batch_args_is_noop_on_posix() -> None:
    """Non-Windows platforms should return argv unchanged."""
    argv = ["build.cmd", "arg^1"]
    assert controller.escape_windows_batch_args(argv) == argv, "Assertion failed"


@pytest.mark.usefixtures("windows_platform")
@pytest.mark.parametrize(
    ("which_result", "argv", "expected"),
    [
        (
            None,
            ["build.cmd", "arg^1", "safe"],
            ["build.cmd", "arg^^^^1", "safe"],
        ),
        (
            lambda cmd: f"C:/tools/{cmd}.cmd",
            ["builder", "^caret"],
            ["builder", "^^^^caret"],
        ),
        (
            lambda _cmd: None,
            ["builder", "^caret"],
            ["builder", "^caret"],
        ),
        (
            lambda cmd: f"C:/bin/{cmd}.exe",
            ["builder", "^"],
            ["builder", "^"],
        ),
        (
            None,
            ["build.cmd", "%PATH%", ""],
            ["build.cmd", "%PATH%", ""],
        ),
    ],
    ids=[
        "explicit-cmd-extension",
        "pathext-resolves-to-batch",
        "missing-on-path",
        "non-batch-resolution",
        "percent-and-empty-untouched",
    ],
)
def test_escape_batch_args_on_windows(
    monkeypatch: pytest.MonkeyPatch,
    which_result: cabc.Callable[[str], str | None] | None,
    argv: list[str],
    expected: list[str],
) -> None:
    """Caret escaping should follow how the command resolves on Windows.

    ``which_result`` of ``None`` leaves the real ``shutil.which`` in place so
    the explicit ``.cmd`` suffix drives the decision.
    """
    if which_result is not None:
        monkeypatch.setattr(controller.shutil, "which", which_result)

    escaped = controller.escape_windows_batch_args(argv)

    assert escaped == expected, f"unexpected escaping for {argv!r}"
