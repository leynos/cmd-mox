"""Shared stand-ins for the Windows modules the IPC client imports.

The client's named-pipe helpers run on any platform once ``pywintypes``,
``win32file`` and ``win32pipe`` are replaced, so the Windows unit-test modules
share one substitution instead of repeating it. Keeping it here also keeps each
module's own cohesion about the helper it covers.
"""

from __future__ import annotations

import types
import typing as typ

from cmd_mox.ipc import client

if typ.TYPE_CHECKING:
    import pytest

TIMEOUT_MESSAGE = "IPC client operation timed out"


class DummyPipeError(Exception):
    """Stand-in for ``pywintypes.error`` carrying a Windows error code."""

    def __init__(self, winerror: int) -> None:
        """Store the Windows error code alongside the exception message."""
        super().__init__(winerror)
        self.winerror = winerror


def patch_windows_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub Windows-only modules so the named-pipe client runs anywhere.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Fixture used to install, and later undo, each substitution.
    """
    monkeypatch.setattr("cmd_mox._path_utils.IS_WINDOWS", True)
    monkeypatch.setattr(
        client,
        "pywintypes",
        types.SimpleNamespace(error=DummyPipeError),
    )
    monkeypatch.setattr(
        client,
        "win32file",
        types.SimpleNamespace(
            CloseHandle=lambda _handle: None,
            WriteFile=lambda *_args, **_kwargs: None,
            FlushFileBuffers=lambda *_args, **_kwargs: None,
        ),
    )
    monkeypatch.setattr(
        client,
        "win32pipe",
        types.SimpleNamespace(WaitNamedPipe=lambda *_args, **_kwargs: None),
    )
