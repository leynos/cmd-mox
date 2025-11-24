"""Bootstrap helpers for cmd_mox.shim."""

from __future__ import annotations

import contextlib
import importlib
import sys
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
            removed.append(entry)
    std_platform = importlib.import_module("platform")
    sys.modules["platform"] = std_platform
    for entry in reversed(removed):
        sys.path.insert(0, entry)
    _BOOTSTRAP_DONE = True
