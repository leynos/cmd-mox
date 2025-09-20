"""Python-native command mocking built around a record-replay-verify lifecycle.

For an overview of the architecture and guiding design, see the project
documentation (`https://github.com/leynos/cmd-mox/blob/main/docs/contents.md`).
"""

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
from .platform import (
    PLATFORM_OVERRIDE_ENV,
    skip_if_unsupported,
    unsupported_reason,
)
from .platform import (
    is_supported as is_supported_platform,
)
from .pytest_plugin import cmd_mox as cmd_mox_fixture
from .shimgen import SHIM_PATH, create_shim_symlinks

is_supported = is_supported_platform

__all__ = [
    "PLATFORM_OVERRIDE_ENV",
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
    "is_supported",
    "is_supported_platform",
    "skip_if_unsupported",
    "temporary_env",
    "unsupported_reason",
]
