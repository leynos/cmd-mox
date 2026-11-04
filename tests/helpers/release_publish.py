"""Reusable fixtures for PyPI release publication tests."""

from __future__ import annotations

import hashlib
import json
import typing as typ

if typ.TYPE_CHECKING:
    from pathlib import Path


class _IndexFetchCase(typ.NamedTuple):
    """Expected tool behaviour for one PyPI index response sequence."""

    statuses: str
    expected_status: int
    expected_calls: int
    expected_sleeps: int


def _tool_arguments(call: dict[str, object]) -> list[str]:
    """Return the argument vector from one recorded fake-tool call.

    Returns
    -------
    list[str]
        The recorded command arguments.
    """
    return typ.cast("list[str]", call["argv"])


def _write_index(path: Path, filenames: list[str]) -> None:
    """Write a minimal PEP 691 index containing the supplied filenames."""
    path.write_text(
        json.dumps({
            "files": [
                {
                    "filename": name,
                    "hashes": {"sha256": hashlib.sha256(name.encode()).hexdigest()},
                    "url": f"https://files.example.invalid/{name}",
                }
                for name in filenames
            ]
        }),
        encoding="utf-8",
    )
