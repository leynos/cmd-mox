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
from .pytest_plugin import cmd_mox as cmd_mox_fixture
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
    "cmd_mox_fixture",
    "create_shim_symlinks",
]
