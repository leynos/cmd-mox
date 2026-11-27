"""Tests for the cmd_mox.ipc public interface."""

from __future__ import annotations

import importlib


def test_ipc_public_api_exports_expected_symbols() -> None:
    """cmd_mox.ipc should re-export key helpers and constants."""
    ipc = importlib.import_module("cmd_mox.ipc")

    expected = {
        "KIND_INVOCATION",
        "KIND_PASSTHROUGH_RESULT",
        "MESSAGE_KINDS",
        "invoke_server",
        "report_passthrough_result",
        "RetryConfig",
    }

    assert expected.issubset(set(ipc.__all__)), "Assertion failed"


def test_message_kinds_align_with_constants() -> None:
    """MESSAGE_KINDS should enumerate the defined protocol kinds."""
    from cmd_mox.ipc import (
        KIND_INVOCATION,
        KIND_PASSTHROUGH_RESULT,
        MESSAGE_KINDS,
    )

    assert set(MESSAGE_KINDS) == {KIND_INVOCATION, KIND_PASSTHROUGH_RESULT}, (
        "Assertion failed"
    )


def test_random_alias_points_to_the_retry_jitter_source() -> None:
    """The deprecated random alias mirrors the module that draws jitter."""
    from cmd_mox.ipc import _retry, random

    assert random is _retry.random, "Assertion failed"
