"""Python-native command mocking built around a record-replay-verify lifecycle.

For an overview of the architecture and guiding design, see the project
documentation (`https://github.com/leynos/cmd-mox/blob/main/docs/contents.md`).
"""

from __future__ import annotations

import importlib
import typing as t

from .comparators import Any, Contains, IsA, Predicate, Regex, StartsWith
from .controller import CmdMox
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
from .ipc import Invocation, IPCServer, NamedPipeServer, Response
from .platform import (
    PLATFORM_OVERRIDE_ENV,
    is_supported,
    skip_if_unsupported,
    unsupported_reason,
)
from .shimgen import SHIM_PATH, create_shim_symlinks
from .test_doubles import CommandDouble, MockCommand, SpyCommand, StubCommand

if t.TYPE_CHECKING:
    from types import ModuleType as _ModuleType
else:  # pragma: no cover - typing fallback only
    _ModuleType = type(importlib)

type _CmdMoxFixture = t.Callable[..., object]

_CMD_MOX_FIXTURE_PYTEST_REQUIRED_MESSAGE: t.Final[str] = (
    "cmd_mox_fixture requires pytest; install 'pytest' to use the fixture."
)


@t.overload
def __getattr__(name: t.Literal["cmd_mox_fixture"]) -> _CmdMoxFixture: ...


@t.overload
def __getattr__(name: str) -> _ModuleType: ...


def __getattr__(name: str) -> _ModuleType | _CmdMoxFixture:
    """Lazily import optional dependencies when requested."""
    if name == "cmd_mox_fixture":
        try:
            from .pytest_plugin import cmd_mox as _cmd_mox_fixture
        except ModuleNotFoundError as exc:  # pytest optional at runtime
            raise RuntimeError(_CMD_MOX_FIXTURE_PYTEST_REQUIRED_MESSAGE) from exc
        globals()[name] = _cmd_mox_fixture
        return _cmd_mox_fixture

    try:
        module = importlib.import_module(f"{__name__}.{name}")
    except ModuleNotFoundError as exc:
        if exc.name in {f"{__name__}.{name}", name}:
            raise AttributeError(name) from exc
        raise

    globals()[name] = module
    return module


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
    "NamedPipeServer",
    "Predicate",
    "Regex",
    "Response",
    "SpyCommand",
    "StartsWith",
    "StubCommand",
    "UnexpectedCommandError",
    "UnfulfilledExpectationError",
    "VerificationError",
    "cmd_mox_fixture",
    "create_shim_symlinks",
    "is_supported",
    "skip_if_unsupported",
    "temporary_env",
    "unsupported_reason",
]
