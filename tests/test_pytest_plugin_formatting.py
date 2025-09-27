"""Unit tests covering teardown failure formatting helpers."""

from __future__ import annotations

import pytest

from cmd_mox.pytest_plugin import _format_single_error


class _BoomError(Exception):
    """Marker exception for formatting expectations."""


@pytest.mark.parametrize(
    ("nodeid", "expected"),
    [
        (None, "cmd_mox verification _BoomError: boom"),
        ("suite::test", "cmd_mox verification for suite::test _BoomError: boom"),
    ],
)
def test_format_single_error_includes_node_context(
    nodeid: str | None, expected: str
) -> None:
    """Ensure verification failures preserve per-item node context."""
    message = _format_single_error(("verification", _BoomError("boom")), nodeid=nodeid)

    assert message == expected
