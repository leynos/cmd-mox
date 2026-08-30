"""Shared typing helpers for invocation-building test factories."""

from __future__ import annotations

import typing as typ


class InvocationKwargs(typ.TypedDict, total=False):
    """Keyword arguments accepted by the ``_make_invocation`` factories."""

    command: str
    args: list[str] | None
    stdin: str
    env: dict[str, str] | None
