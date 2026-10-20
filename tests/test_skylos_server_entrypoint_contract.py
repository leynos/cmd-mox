"""Verify dynamic Skylos server entrypoints against source scope.

Run with ``uv run pytest -q tests/test_skylos_server_entrypoint_contract.py``.
"""

from __future__ import annotations

import ast
import tomllib
import typing as typ
from pathlib import Path

if typ.TYPE_CHECKING:
    import collections.abc as cabc

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_SERVER_MODULE = "cmd_mox.ipc.server"
_SERVER_PATH = "cmd_mox/ipc/server.py"
_ENTRYPOINT_TYPES = {
    f"{_SERVER_MODULE}._inner_server_cls_for_backend": "function",
    f"{_SERVER_MODULE}._backend_error_category": "function",
    f"{_SERVER_MODULE}._inner_server_cls": "function",
    f"{_SERVER_MODULE}._InnerServer": "class",
}
_SOURCE_DEFINITION_PATHS = {
    f"{_SERVER_MODULE}._inner_server_cls_for_backend": (
        f"{_SERVER_MODULE}._inner_server_cls_for_backend"
    ),
    f"{_SERVER_MODULE}._backend_error_category": (
        f"{_SERVER_MODULE}._backend_error_category"
    ),
    f"{_SERVER_MODULE}._inner_server_cls": f"{_SERVER_MODULE}._inner_server_cls",
    f"{_SERVER_MODULE}._InnerServer": (
        f"{_SERVER_MODULE}._inner_server_cls._InnerServer"
    ),
}


def _qualified_definitions(
    node: ast.AST, prefix: str
) -> cabc.Iterator[tuple[str, str]]:
    """Yield qualified function and class paths, preserving lexical scope.

    Yields
    ------
    tuple[str, str]
        The qualified source path and the definition kind.
    """
    for child in ast.iter_child_nodes(node):
        match child:
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                qualified_name = f"{prefix}.{name}"
                yield qualified_name, "function"
                yield from _qualified_definitions(child, qualified_name)
            case ast.ClassDef(name=name):
                qualified_name = f"{prefix}.{name}"
                yield qualified_name, "class"
                yield from _qualified_definitions(child, qualified_name)
            case _:
                yield from _qualified_definitions(child, prefix)


def test_dynamic_server_entrypoints_match_configured_source_scopes() -> None:
    """Keep Skylos names and types bound to the intended server definitions."""
    pyproject = tomllib.loads(
        (_REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    entrypoints = pyproject["tool"]["skylos"]["dead_code"]["entrypoints"]
    dynamic_entrypoints = [
        entrypoint
        for entrypoint in entrypoints
        if set(entrypoint.get("full_name", ())) & _ENTRYPOINT_TYPES.keys()
    ]
    assert dynamic_entrypoints, "Skylos must configure dynamic server entrypoints."
    assert all(
        entrypoint.get("path") == _SERVER_PATH for entrypoint in dynamic_entrypoints
    ), "All dynamic server entrypoints must name the server module."

    configured_types = {
        full_name: entrypoint["type"]
        for entrypoint in dynamic_entrypoints
        for full_name in entrypoint["full_name"]
    }
    assert configured_types == _ENTRYPOINT_TYPES, (
        "Configured entrypoint names and types must match the expected server hooks."
    )

    server_source = (_REPOSITORY_ROOT / _SERVER_PATH).read_text(encoding="utf-8")
    tree = ast.parse(server_source)
    source_definitions = set(_qualified_definitions(tree, _SERVER_MODULE))
    expected_source_definitions = {
        (_SOURCE_DEFINITION_PATHS[full_name], definition_type)
        for full_name, definition_type in configured_types.items()
    }
    assert expected_source_definitions <= source_definitions, (
        "Every configured entrypoint must match a definition at its expected scope."
    )
