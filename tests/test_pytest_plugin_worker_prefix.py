"""Regression tests for the top-level pytest plugin helpers."""

from __future__ import annotations

import typing as t

from cmd_mox.pytest_plugin import _worker_prefix

if t.TYPE_CHECKING:  # pragma: no cover - imported for typing only
    import pytest


class _StubConfig:
    """Mimic ``pytest.Config`` with a customizable ``workerinput`` attribute."""

    __slots__ = ("workerinput",)

    def __init__(self, workerinput: object | None = None) -> None:
        self.workerinput = workerinput


class _StubRequest:
    """Minimal request stub exposing the ``config`` attribute."""

    __slots__ = ("config",)

    def __init__(self, config: _StubConfig) -> None:
        self.config = config


def test_worker_prefix_uses_mapping_workerinput(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ensure xdist-style dict payloads produce unique worker prefixes."""
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)

    request = _StubRequest(config=_StubConfig(workerinput={"workerid": "gw-dict"}))

    prefix = _worker_prefix(request)  # type: ignore[arg-type]

    assert prefix.startswith("cmdmox-gw-dict-")
