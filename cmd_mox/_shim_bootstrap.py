"""Bootstrap helpers for cmd_mox.shim."""

from __future__ import annotations

import contextlib
import importlib
import sys
from pathlib import Path


def bootstrap_shim_path() -> None:
    """Ensure stdlib modules win over cmd_mox shims."""
    package_dir = Path(__file__).resolve().parent
    removed: list[str] = []
    for entry in list(sys.path):
        remove_entry = False
        if entry.startswith("__editable__"):
            remove_entry = True
        else:
            with contextlib.suppress(FileNotFoundError):
                if Path(entry).resolve() == package_dir:
                    remove_entry = True
        if remove_entry:
            sys.path.remove(entry)
            removed.append(entry)
    std_platform = importlib.import_module("platform")
    sys.modules["platform"] = std_platform
    for entry in reversed(removed):
        sys.path.insert(0, entry)
