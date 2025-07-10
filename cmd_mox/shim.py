#!/usr/bin/env python3
"""Generic command shim for CmdMox."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    """Entry point for the shim."""
    cmd_name = Path(sys.argv[0]).name
    print(cmd_name)


if __name__ == "__main__":  # pragma: no cover - manual entry
    main()
