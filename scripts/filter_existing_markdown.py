#!/usr/bin/env python3
"""Filter NUL-delimited Git paths to Markdown files in the working tree."""

import os
import sys
from pathlib import Path


def _existing_markdown_paths(paths: bytes) -> bytes:
    """Return existing file paths from a NUL-delimited Git listing.

    Returns
    -------
    bytes
        Existing paths with their NUL delimiters preserved.
    """
    return b"".join(
        path + b"\0"
        for path in paths.split(b"\0")
        if path and Path(os.fsdecode(path)).is_file()
    )


def main() -> None:
    """Filter the Git path list from standard input and write it to standard output."""
    sys.stdout.buffer.write(_existing_markdown_paths(sys.stdin.buffer.read()))


if __name__ == "__main__":
    main()
