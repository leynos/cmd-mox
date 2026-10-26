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


class StdinTimeoutError(TimeoutError):
    """The shim could not capture stdin before ``CMOX_IPC_TIMEOUT`` expired.

    Parameters
    ----------
    timeout:
        The deadline in seconds that elapsed while waiting for stdin.
    worker_alive:
        Whether a reader worker was still blocked when the error was raised.
        A live worker means the abandoned read could not be interrupted and
        the resource it holds will only be released once the stream closes.

    Notes
    -----
    The message never includes captured text, so partial input cannot leak
    into a diagnostic or a log record. ``worker_alive`` is deliberately absent
    from the text: the shim prints this message verbatim to command callers.
    """

    def __init__(self, timeout: float, *, worker_alive: bool) -> None:
        self.timeout = timeout
        self.worker_alive = worker_alive
        # The diagnostic is shown to command callers, so it must stay stable
        # and free of internal reader state; ``worker_alive`` remains available
        # to telemetry and to the script entry point.
        super().__init__(f"stdin read timed out after {timeout:g} seconds")
