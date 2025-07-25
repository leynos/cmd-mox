"""cmd-mox package."""

from __future__ import annotations

from .comparators import Any, Contains, IsA, Predicate, Regex, StartsWith
from .controller import CmdMox, CommandDouble, MockCommand, SpyCommand
from .environment import EnvironmentManager, temporary_env
from .errors import (
    CmdMoxError,
    LifecycleError,
    MissingEnvironmentError,
    UnexpectedCommandError,
    UnfulfilledExpectationError,
    VerificationError,
)
from .expectations import Expectation
from .ipc import Invocation, IPCServer, Response
from .pytest_plugin import cmd_mox as cmd_mox_fixture
from .shimgen import SHIM_PATH, create_shim_symlinks

__all__ = [
    "SHIM_PATH",
    "Any",
    "CmdMox",
    "CmdMoxError",
    "CommandDouble",
    "Contains",
    "EnvironmentManager",
    "Expectation",
    "IPCServer",
    "Invocation",
    "IsA",
    "LifecycleError",
    "MissingEnvironmentError",
    "MockCommand",
    "Predicate",
    "Regex",
    "Response",
    "SpyCommand",
    "StartsWith",
    "UnexpectedCommandError",
    "UnfulfilledExpectationError",
    "VerificationError",
    "cmd_mox_fixture",
    "create_shim_symlinks",
    "temporary_env",
]
