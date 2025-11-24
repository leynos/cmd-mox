"""Bootstrap helpers for cmd_mox.shim."""

from __future__ import annotations

import contextlib
import importlib
import importlib.util as importlib_util
import os
import sys
import sysconfig
from pathlib import Path

_BOOTSTRAP_DONE = False


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
            sys.path_importer_cache.pop(entry, None)
            resolved_entry = os.fspath(Path(entry).resolve())
            sys.path_importer_cache.pop(resolved_entry, None)
            removed.append(entry)
    sys.path_importer_cache.clear()
    importlib.invalidate_caches()
    stdlib_platform = Path(sysconfig.get_path("stdlib")) / "platform.py"
    spec = importlib_util.spec_from_file_location("platform", stdlib_platform)
    if spec is None or spec.loader is None:
        std_platform = importlib.import_module("platform")
    else:
        std_platform = importlib_util.module_from_spec(spec)
        spec.loader.exec_module(std_platform)
    sys.modules["platform"] = std_platform
    for entry in reversed(removed):
        sys.path.insert(0, entry)
    _BOOTSTRAP_DONE = True
