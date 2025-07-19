"""cmd-mox package."""

from __future__ import annotations

from .controller import CmdMox, CommandDouble, MockCommand, SpyCommand
from .environment import EnvironmentManager
from .errors import (
    CmdMoxError,
    LifecycleError,
    MissingEnvironmentError,
    UnexpectedCommandError,
    UnfulfilledExpectationError,
    VerificationError,
)
from .ipc import Invocation, IPCServer, Response
from .shimgen import SHIM_PATH, create_shim_symlinks

__all__ = [
    "SHIM_PATH",
    "CmdMox",
    "CmdMoxError",
    "CommandDouble",
    "EnvironmentManager",
    "IPCServer",
    "Invocation",
    "LifecycleError",
    "MissingEnvironmentError",
    "MockCommand",
    "Response",
    "SpyCommand",
    "UnexpectedCommandError",
    "UnfulfilledExpectationError",
    "VerificationError",
    "create_shim_symlinks",
]
