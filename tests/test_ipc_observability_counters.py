"""Property checks for bounded IPC observability counter dimensions."""

from __future__ import annotations

import hypothesis.strategies as st
from hypothesis import given

from cmd_mox.ipc import _observability

_UNKNOWN_ERROR_CATEGORY = st.text().map(lambda value: f"unknown:{value}")


@given(
    message_kinds=st.sets(st.text(), min_size=2, max_size=4),
    error_categories=st.sets(_UNKNOWN_ERROR_CATEGORY, min_size=2, max_size=4),
)
def test_counter_key_collapses_arbitrary_message_kinds_and_error_categories(
    message_kinds: set[str], error_categories: set[str]
) -> None:
    """Arbitrary message kinds and unknown categories share one counter key."""
    _observability.reset_counters()
    try:
        for message_kind in message_kinds:
            for error_category in error_categories:
                _observability.emit(
                    _observability.IPCEvent(
                        operation="ipc.dispatch",
                        transport="unix",
                        kind=message_kind,
                        outcome="handler_error",
                        error_category=error_category,
                    )
                )

        counters = _observability.counter_snapshot()

        assert len(counters) == 1, "Unbounded values must collapse to one key"
        [(key, count)] = counters.items()
        assert key.error_category is None, "Unknown categories must not label keys"
        assert count == len(message_kinds) * len(error_categories), (
            "Equivalent events must aggregate under the same key"
        )
    finally:
        _observability.reset_counters()
