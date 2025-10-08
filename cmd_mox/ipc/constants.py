"""Constants shared across IPC client and server implementations."""

from __future__ import annotations

KIND_INVOCATION: str = "invocation"
KIND_PASSTHROUGH_RESULT: str = "passthrough-result"

MESSAGE_KINDS: tuple[str, ...] = (
    KIND_INVOCATION,
    KIND_PASSTHROUGH_RESULT,
)

__all__ = [
    "KIND_INVOCATION",
    "KIND_PASSTHROUGH_RESULT",
    "MESSAGE_KINDS",
]
