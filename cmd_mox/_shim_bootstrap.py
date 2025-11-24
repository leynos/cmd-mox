"""Bootstrap helpers for cmd_mox.shim."""

from __future__ import annotations

import contextlib
import importlib
import importlib.util as importlib_util
import sys
import sysconfig
import typing as t
from pathlib import Path

if t.TYPE_CHECKING:
    from types import ModuleType

_BOOTSTRAP_DONE = False


def _load_stdlib_platform() -> ModuleType:
    """Return the stdlib platform module, falling back to regular import."""
    importlib.invalidate_caches()
    std_platform = None
    stdlib_path: str | None
    try:
        stdlib_path = sysconfig.get_path("stdlib")
    except (KeyError, OSError, ValueError, TypeError):
        stdlib_path = None

    if stdlib_path:
        try:
            stdlib_platform = Path(stdlib_path) / "platform.py"
            if stdlib_platform.is_file():
                spec = importlib_util.spec_from_file_location(
                    "platform", stdlib_platform
                )
                if spec is not None and spec.loader is not None:
                    candidate_module = importlib_util.module_from_spec(spec)
                    try:
                        spec.loader.exec_module(candidate_module)
                        std_platform = candidate_module
                    except (FileNotFoundError, OSError, ImportError):
                        std_platform = None
        except (OSError, ValueError):
            std_platform = None

    return t.cast("ModuleType", std_platform or importlib.import_module("platform"))


def _should_remove_path_entry(entry: str, package_dir: Path) -> bool:
    if entry.startswith("__editable__"):
        return True
    with contextlib.suppress(FileNotFoundError):
        if Path(entry).resolve() == package_dir:
            return True
    return False


def bootstrap_shim_path() -> None:
    """Ensure stdlib modules win over cmd_mox shims."""
    global _BOOTSTRAP_DONE
    if _BOOTSTRAP_DONE:
        return

    package_dir = Path(__file__).resolve().parent
    removed: list[str] = []
    for entry in list(sys.path):
        if _should_remove_path_entry(entry, package_dir):
            sys.path.remove(entry)
            removed.append(entry)
    sys.path_importer_cache.clear()
    std_platform = _load_stdlib_platform()
    sys.modules["platform"] = std_platform
    for entry in reversed(removed):
        sys.path.insert(0, entry)
    _BOOTSTRAP_DONE = True
