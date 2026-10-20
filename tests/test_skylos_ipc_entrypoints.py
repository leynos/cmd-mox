"""Keep IPC server Skylos entry points tied to their source definitions."""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_ENTRYPOINTS = {
    "cmd_mox.ipc.server._inner_server_cls": ("function", "cmd_mox/ipc/server.py"),
    "cmd_mox.ipc.server._InnerServer": ("class", "cmd_mox/ipc/server.py"),
}


def test_unix_server_factory_entrypoints_match_definitions() -> None:
    """Keep the Skylos exceptions tied to the live factory and class."""
    config = tomllib.loads(
        (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    configured_entries = config["tool"]["skylos"]["dead_code"]["entrypoints"]
    entries = {
        full_name: entrypoint
        for entrypoint in configured_entries
        for full_name in entrypoint["full_name"]
        if full_name in EXPECTED_ENTRYPOINTS
    }
    assert set(entries) == set(EXPECTED_ENTRYPOINTS), (
        "Unix server entrypoint config must match the reviewed factory symbols."
    )
    assert {
        full_name: (entrypoint["type"], entrypoint["path"])
        for full_name, entrypoint in entries.items()
    } == EXPECTED_ENTRYPOINTS, (
        "Unix server entrypoint config must retain reviewed types and file scope."
    )

    server_path = REPOSITORY_ROOT / "cmd_mox/ipc/server.py"
    tree = ast.parse(server_path.read_text(encoding="utf-8"))
    definitions = {
        (node.name, "function")
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    } | {
        (node.name, "class")
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }
    expected_definitions = {
        (full_name.rsplit(".", maxsplit=1)[-1], entry_type)
        for full_name, (entry_type, _) in EXPECTED_ENTRYPOINTS.items()
    }
    assert expected_definitions <= definitions, (
        "Unix server entrypoints must resolve to current source definitions."
    )
