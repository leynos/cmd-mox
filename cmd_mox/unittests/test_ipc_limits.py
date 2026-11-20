"""Tests for the transport-neutral IPC limits and size error."""

from __future__ import annotations

from cmd_mox.ipc import windows
from cmd_mox.ipc._limits import MAX_ACTIVE_CLIENTS, MAX_MESSAGE_SIZE
from cmd_mox.ipc._limits import MessageTooLargeError as SharedSizeError
from cmd_mox.ipc.windows import PipeMessageTooLargeError


def test_shared_limits_name_the_agreed_bounds() -> None:
    """Both transports share one 8 MiB message cap and one 64-client cap."""
    assert MAX_MESSAGE_SIZE == 8 * 1024 * 1024, "the message cap changed"
    assert MAX_ACTIVE_CLIENTS == 64, "the active-client cap changed"
    assert windows.MAX_MESSAGE_SIZE is MAX_MESSAGE_SIZE, "windows did not re-export"


def test_shared_size_error_carries_only_byte_counts() -> None:
    """The shared error reports counts and never the payload."""
    error = SharedSizeError(received=12, limit=8)

    assert error.received == 12, "the received count was not stored"
    assert error.limit == 8, "the limit was not stored"
    assert str(error) == "message exceeded the 8-byte limit", "message changed"
    assert vars(error) == {"received": 12, "limit": 8}, "unexpected error fields"


def test_pipe_size_error_keeps_its_established_contract() -> None:
    """The named-pipe spelling stays a subclass with its original message."""
    error = PipeMessageTooLargeError(received=12, limit=8)

    assert isinstance(error, SharedSizeError), "the pipe error is not a subclass"
    assert error.args == ("named pipe message exceeded the 8-byte limit",), (
        "the pipe error message or args changed"
    )
    assert vars(error) == {"received": 12, "limit": 8}, "unexpected error fields"
