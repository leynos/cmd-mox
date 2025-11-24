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


def _get_stdlib_path() -> str | None:
    """Return the stdlib path, guarding against missing or invalid configs."""
    try:
        return sysconfig.get_path("stdlib")
    except (KeyError, OSError, ValueError, TypeError):
        return None


def _create_module_from_file(module_name: str, file_path: Path) -> ModuleType | None:
    """Load and return a module from *file_path*, or ``None`` on failure."""
    try:
        if not file_path.is_file():
            return None
        spec = importlib_util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib_util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except (FileNotFoundError, OSError, ImportError):
            return None
        else:
            return module
    except (OSError, ValueError):
        return None


def _load_stdlib_platform() -> ModuleType:
    """Return the stdlib platform module, falling back to regular import."""
    importlib.invalidate_caches()
    stdlib_path = _get_stdlib_path()

    if stdlib_path:
        platform_path = Path(stdlib_path) / "platform.py"
        module = _create_module_from_file("platform", platform_path)
        if module is not None:
            return module

    return importlib.import_module("platform")


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
