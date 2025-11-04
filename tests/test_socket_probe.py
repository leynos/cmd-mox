"""Tests for Unix socket capability detection helpers."""

from __future__ import annotations

import typing as t

import conftest as ct

if t.TYPE_CHECKING:
    import pytest


def test_can_bind_unix_socket_handles_missing_af_unix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_can_bind_unix_socket should return False when AF_UNIX is unavailable."""

    class DummySocketModule:
        SOCK_STREAM = object()

    dummy_socket = DummySocketModule()

    monkeypatch.setattr(ct, "socket", dummy_socket)

    assert ct._can_bind_unix_socket() is False
