"""Custom exceptions for CmdMox."""

from __future__ import annotations


class CmdMoxError(Exception):
    """Base exception for CmdMox errors."""


class LifecycleError(CmdMoxError):
    """Operation performed in an invalid lifecycle phase."""


class MissingEnvironmentError(CmdMoxError):
    """Required environment attribute is missing."""

    DEFAULT_MESSAGE = "Replay environment is not ready"


class VerificationError(CmdMoxError):
    """Base class for verification-related errors."""


class UnexpectedCommandError(VerificationError):
    """An unexpected command was invoked during replay."""


class UnfulfilledExpectationError(VerificationError):
    """A stub or expectation was not called during replay."""
