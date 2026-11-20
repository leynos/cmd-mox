"""Test helpers for injecting failures into :mod:`cmd_mox.atomic_write`."""

from __future__ import annotations

import collections.abc as cabc
import typing as typ
from pathlib import Path

from cmd_mox import atomic_write

if typ.TYPE_CHECKING:
    import os

    import pytest

_ReplaceHook = cabc.Callable[[Path, Path], Path]


def _make_failing_path_type(replace: _ReplaceHook) -> type[Path]:
    """Return a ``Path`` subclass that delegates ``replace`` to *replace*."""

    class _InjectedPath(Path):
        """Path whose ``replace`` runs the injected behaviour."""

        def replace(self, target: str | os.PathLike[str]) -> typ.Self:
            """Run *replace* instead of the real replacement."""
            # The base signature accepts any path-like, so normalise before
            # handing the destination to the hook.
            return typ.cast("typ.Self", replace(self, Path(target)))

    return _InjectedPath


def install_replace_hook(
    monkeypatch: pytest.MonkeyPatch,
    replace: _ReplaceHook,
) -> None:
    """Route the atomic writer's replacements through *replace*.

    Patching ``pathlib.Path.replace`` directly would make every replacement in
    the process fail until the test ends. Rebinding only the module global
    keeps the injection local to the paths this helper constructs.
    """
    monkeypatch.setattr(atomic_write, "Path", _make_failing_path_type(replace))
