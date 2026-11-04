"""Custom exceptions for CmdMox."""

from __future__ import annotations


class CmdMoxError(Exception):
    """Base exception for CmdMox errors."""


class LifecycleError(CmdMoxError):
    """Operation performed in an invalid lifecycle phase."""


class ExpectationConfigurationError(CmdMoxError):
    """Raised when a command double receives a second argument expectation.

    Notes
    -----
    This occurs when ``with_args()`` or ``with_matching_args()`` is called
    after an argument expectation has already been configured. Use
    ``cmd_mox.spy(name).runs(handler)`` to handle several calls to one command.
    Sensitive option values are redacted from the error message.
    """


class MissingEnvironmentError(CmdMoxError):
    """Required environment attribute is missing."""

    DEFAULT_MESSAGE = "Replay environment is not ready"


class VerificationError(CmdMoxError):
    """Base class for verification-related errors."""


class UnexpectedCommandError(VerificationError):
    """An unexpected command was invoked during replay."""


class UnfulfilledExpectationError(VerificationError):
    """A stub or expectation was not called during replay."""
